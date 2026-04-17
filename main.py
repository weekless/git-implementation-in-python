from __future__ import annotations
import argparse
from pathlib import Path
import hashlib
import zlib
import sys
import time
from typing import Dict, List, Tuple

import json


class GitObject:
    def __init__(self, objType: str, content: bytes):
        self.type = objType
        self.content = content
        
    def hash(self) -> str:
        # f(<type> <size>\0<content>)
        header = f"{self.type} {len(self.content)}\0".encode()
        return hashlib.sha1(header + self.content).hexdigest()
    
    def serialise(self) -> bytes:
        header = f"{self.type} {len(self.content)}\0".encode()
        return zlib.compress(header + self.content)
    
    @classmethod
    def deserialise(cls, data: bytes) -> GitObject:
        decompressed = zlib.decompress(data)
        nullIdx = decompressed.find(b"\0")
        header = decompressed[:nullIdx].decode()
        content = decompressed[nullIdx + 1:]
        
        objType, _ = header.split(" ")
        
        return cls(objType, content)
    
class Blob(GitObject):
    def __init__(self, content: bytes):
        super().__init__("blob", content)
        
    def getContent(self) -> bytes:
        return self.content
    
class Tree(GitObject):
    def __init__(self, entries: List[Tuple[str, str, str]]):
        self.entries = entries or []
        content = self.serialiseEntries()
        super().__init__("tree", content)
        
    def _serialiseEntries(self) -> bytes:
        # <mode> <name>\0<hash>
        content = b""
        for mode, name, objHash in sorted(self.entries):
            content += f"{mode} {name}\0".encode()
            content += bytes.fromhex(objHash)
        
        return content

    def addEntry(self, mode: str, name: str, objHash: str):
        self.entries.append(mode, name, objHash)
        self.content = self._serialiseEntries()
        
    @classmethod
    def fromContent(cls, content: bytes) -> Tree:
        tree = cls()
        i = 0
        while i < len(content):
            nullIdx = content.find(b"\0", i)
            if nullIdx == -1:
                break
            
            modeAndName = content[i:nullIdx]
            mode, name = modeAndName.split(" ", 1)
            objHash = content[nullIdx + 1: nullIdx + 21]. hex()
            tree.entries.append((mode, name, objHash))
            
            i = nullIdx + 21
            
        return tree

class Commit(GitObject):
    def __init__(self, treeHash: str, parentHashes: List[str], 
                 author: str, committer: str, message: str, timestamp: int = None):
        self.treeHash = treeHash
        self.parentHashes = parentHashes
        self.author = author
        self.committer = committer
        self.message = message
        self.timestamp = timestamp or int(time.time())
        content = self._serialiseCommit()
        super().__init__("commit", content)
        
        
    def _serialiseCommit(self):
        # tree <tree hash> \n parent <parent hash> \n author <name> <timestamp> timezone
        lines = [f"tree {self.treeHash}"]
        for parent in self.parentHashes:
            lines.append(f"parent {parent}")
            
        lines.append(f"author {self.author} {self.timestamp} +0000")
        lines.append(f"committer {self.committer} {self.timestamp} +0000")
        lines.append("")
        lines.append(self.message)
        
        return "\n".join(lines).encode()
    
    @classmethod
    def fromContent(cls, content : bytes) -> Commit:
        lines = content.decode().split("\n")
        treeHash = None
        parentHashes = []
        author = None
        committer = None
        messageStart = 0
        
        for i, line in enumerate(lines):
            if line.startswith("tree "):
                treeHash = lines[5:]
            elif line.startswith("parent "):
                parentHashes.append(line[7:])
            elif line.startswith("author "):
                authorParts = line[7:].rsplit(" ", 2)
                author = authorParts[0]
                timestamp = int(authorParts[1])
            elif line.startswith("committer "):
                committerParts = line[10:].rsplit(" ", 2)
                committer = committerParts[0]
            elif line == "":
                messageStart = i+1
                break
        message = "\n".join(lines[messageStart:])
        commit = cls(treeHash, parentHashes, author, committer, message, timestamp)
        return commit
        



class Repository:
    def __init__(self, path="."):
        self.path = Path(path).resolve()
        self.gitDir = self.path / ".pygit"
        
        # .git/objects
        self.objectDir = self.gitDir / "objects"
        
        # .git/refs
        self.refDir = self.gitDir / "refs"
        self.headsDir = self.refDir / "heads"
        
        # HEAD file
        self.headFile = self.gitDir / "HEAD"
        
        # .git/index
        self.indexFile = self.gitDir / "index"
        
    def init(self) -> bool:
        if self.gitDir.exists():
            return False
        
        # create directories
        self.gitDir.mkdir(parents=True)
        self.objectDir.mkdir(parents=True)
        self.refDir.mkdir(parents=True)
        self.headsDir.mkdir(parents=True)
        
        # create initial HEAD pointing to a branch
        self.headFile.write_text("ref: refs/heads/main\n")
        
        self.saveIndex({})
        
        print(f"Initialised empty Git repository in {self.gitDir}")
        
        return True
        
    def storeObject(self, obj: GitObject) -> str:
        objHash = obj.hash()
        objDir = self.objectDir / objHash[:2]
        objFile = objDir / objHash[2:]
        
        if not objFile.exists():
            objDir.mkdir(exist_ok=True)
            objFile.write_bytes(obj.serialise())
            
        return objHash
        
    def loadIndex(self) -> Dict[str, str]:
        if not self.indexFile.exists():
            return {}
        
        try:
            return json.loads(self.indexFile.read_text())
        except:
            return {}
        
    
    def saveIndex(self, index: Dict[str, str]):
        self.indexFile.write_text(json.dumps(index, indent=2))
    
    def addFile(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} not found")

        # Read file content
        content = fullPath.read_bytes()
        
        # Create Binary Large Object (BLOB) from the content
        blob = Blob(content)
        
        # Store the BLOB in database (.pygit/objects)
        blobHash = self.storeObject(blob)
        
        # Update index to include the file
        index = self.loadIndex()
        index[path] = blobHash
        self.saveIndex(index)
         
        print(f"Added {path}")
        
        pass
    
    def addDirectory(self, path: str):
        fullPath = self.path / path
        if not fullPath.exists():
            raise FileNotFoundError(f"Path {path} not found")
        if not fullPath.is_dir():
            raise ValueError(f"{path} is not a directory")
        index = self.loadIndex()
        count = 0
        # Traverse the directory
        for filePath in fullPath.rglob("*"):
            if filePath.is_file():
                if ".pygit" in filePath.parts:
                    continue
                # Create and store blob object
                content = filePath.read_bytes()
                blob = Blob(content)
                blobHash = self.storeObject(blob)
                # Update index
                relativePath = str(filePath.relative_to(self.path))
                index[relativePath] = blobHash
                count += 1
                
        self.saveIndex(index)  
        
        if count > 0:
            print(f"Added {count} files from directory {path}")
        else:
            print(f"Directory {path} already up to date")
        
        
    
    def addPath(self, path: str) -> None:
        fullPath = self.path / path
        
        if not fullPath:
            raise FileNotFoundError(f"Path {path} not found")
        if fullPath.is_file():
            self.addFile(path)
        elif fullPath.is_dir():
            self.addDirectory(path)
        else:
            raise ValueError(f"{path} is neither a file nor a directory" )
                
    def loadObject(self, objHash: str) -> GitObject:
        objDir = self.objectDir / objHash[:2]
        objFile = objDir / objHash[2:]
        
        if not objFile.exists():
            raise FileNotFoundError(f"Object {objHash} not found")
            
        return GitObject.deserialise(objFile.read_bytes())
            
            
    def createTreeFromIndex(self):
        index = self.loadIndex()
        if not index:
            tree = Tree()
            return self.storeObject(tree)
            
        dirs = {}
        files = {}
        
        for filePath, blobHash in index.items():
            parts = filePath.split("/")
            if len(parts) == 1:
                files[parts[0]] = blobHash
            else:
                dirName = parts[0]
                if dirName not in dirs:
                    dirs[dirName] = {}
                
                current = dirs[dirName]
                for part in parts[1:-1]:
                    current[part] = {}
                    
                    current = current[part]
                
                current[parts[-1]] = blobHash
                
        def createTreeRecursive(entriesDict: Dict):
            tree = Tree()
            for name, blobHash in entriesDict.items():
                if isinstance(blobHash, str):
                    tree.addEntry("100644", name, blobHash)
                if isinstance(blobHash, dict):
                    subtreeHash = createTreeRecursive(blobHash)
                    tree.addEntry("40000", name, subtreeHash)
                    
            return self.storeObject(tree)
        
        rootEntries = {**files}
        for dirName, dirContent in dirs.items():
            rootEntries[dirName] = dirContent
        pass
    
    def getCurrentBranch(self) -> str:
        if not self.headFile.exists():
            return "main"
        headContent = self.headFile.read_text().strip()
        if headContent.startswith("ref: refs/heads/"):
            return headContent[16:]
        
        # detached HEAD
        return "HEAD"
    
    def getBranchCommit(self, currentBranch: str):
        branchFile = self.headsDir / currentBranch
        
        if branchFile.exists():
            return branchFile.read_text().strip()
        
        return None
    
    def setBranchCommit(self, currentBranch: str, commitHash: str):
        branchFile = self.headsDir / currentBranch
        branchFile.write_text(commitHash + "\n")
    
    
    def commit(self, message: str, author: str = "User"):
        # Create a tree object from the index
        treeHash = self.createTreeFromIndex()
        
        currentBranch = self.getCurrentBranch()
        parentCommit = self.getBranchCommit(currentBranch)
        parentHashes = [parentCommit] if parentCommit else []
        
        # If nothing was added
        index = self.loadIndex()
        if not index:
            print("Nothing to commit, working tree clean") 
            return None
        
        # If no changes are being committed
        if parentCommit:
            parentGitCommitObj = self.loadObject(parentCommit)
            parentCommitContent = Commit.fromContent(parentGitCommitObj.content)
            if treeHash == parentCommitContent.treeHash:
                print("Nothing to commit, working tree clean")
            
        
        commit = Commit(treeHash=treeHash, parentHashes=parentHashes, 
                        author=author, committer=author, message=message)
        
        commitHash = self.storeObject(commit)
        
        # Branch commit with be updated with newest commit
        self.setBranchCommit(currentBranch, commitHash)
        self.saveIndex({})
        print(f"Created commit {commitHash} on branch {currentBranch}")
        return commitHash
        
    
    def getFileFromTree(self, treeHash: str, prefix: str = "") -> set:
        files = set()
        
        try:
            treeObj = self.loadObject(treeHash)
            tree = Tree.fromContent(treeObj.content)
            # list<tuple<str, str, str>>
            for mode, name, objHash in tree.entries:
                fullName = f"{prefix}{name}"
                # file
                if mode.startswith("100"):
                    files.add(fullName)
                # directory
                elif mode.startswith("400"):
                    subtreeFiles = self.getFileFromTree(objHash, f"{fullName}/")
                    files.update(subtreeFiles)
        except Exception as e:
            print(f"Could not read tree {treeHash}: {e}")
            
        return files
    
    def checkout(self, branch: str, createBranch: bool):
        # Computed the files to clear from the previous commit
        previousBranch = self.getCurrentBranch()
        filesToClear = set()
        try:
            previousCommitHash = self.getBranchCommit(previousBranch)
            if previousCommitHash:
                previousCommitObj = self.loadObject(previousCommitHash)
                previousCommit = Commit.fromContent(previousCommitObj.content)
                if previousCommit.treeHash:
                    filesToClear = self.getFileFromTree(previousCommit.treeHash)
        except Exception:
            filesToClear = set()
            
        # Created a new branch 
        branchFile = self.headsDir / branch
        if not branchFile.exists():
            if createBranch:
                if previousCommitHash:
                    self.setBranchCommit(branch, previousCommitHash)
                    print(f"Created new branch {branch}")
                else:
                    print("No commits yet, cannot create a branch")
                    return
            else:
                print(f"Branch '{branch}' not found")
                print(f"Use 'checkout -b {branch}' to create and switch to new branch")
                return 
        self.headFile.write_text(f"ref: refs/heads/{branch}\n")
        
        # Restore working directory 
        self.restoreWorkingDirectory(branch, filesToClear)
        print(f"Switched to branch {branch}")
    
    def restoreTree(self, treeHash: str, path: str):
        treeObj = self.loadObject(treeHash)
        tree = Tree.fromContent(treeObj.content)
        # list<tuple<str, str, str>>
        for mode, name, objHash in tree.entries:
            filePath = self.path / name
            # file
            if mode.startswith("100"):
                blobObj = self.loadObject(objHash)
                blob = Blob(blobObj.content)
                filePath.write_bytes(blob.getContent())
            # directory
            elif mode.startswith("400"):
                filePath.mkdir(exist_ok=True)
                self.restoreTree(objHash, filePath)
                
    
    def restoreWorkingDirectory(self, branch: str, filesToClear: set[str]):
        targetCommitHash = self.getBranchCommit(branch)
        if not targetCommitHash:
            return
        
        # Remove files tracked by previous branch
        for relPath in sorted(filesToClear):
            filePath = self.path / relPath
            try:
                if filePath.is_file():
                    filePath.unlink()
                elif filePath.is_dir():
                    if not any(filePath.iterdir()):
                        filePath.rmdir()
            except Exception:
                pass
            
        targetCommitObj = self.loadObject(targetCommitHash)
        targetCommit = Commit.fromContent(targetCommitObj)
        
        if targetCommit.treeHash:
            self.restoreTree(targetCommit.treeHash, self.path)
            
        self.saveIndex({})
        
def main():
    parser = argparse.ArgumentParser(description="PyGit")
    subparser = parser.add_subparsers(dest="command", help="Available commands")
    
    # init command
    initParser = subparser.add_parser("init", help="Initialise a new repository")
    
    # add command
    addParser = subparser.add_parser("add", help="Add files and directories to staging area")
    addParser.add_argument("paths", nargs="+", help="Files and directories to add")
    
    # commit command
    commitParser = subparser.add_parser("commit", help="Create a new commit")
    commitParser.add_argument("-m", "--message", help="Commit message", required=True)
    commitParser.add_argument("--author", help="Author name")
    
    # checkout command
    checkoutParser = subparser.add_parser("checkout", help="Move/Create a new branch")
    checkoutParser.add_argument("branch", help="Branch to switch to")
    checkoutParser.add_argument("-b", "--create-branch", action="storeTrue")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    repo = Repository()
    try:
        if args.command == "init":
            if not repo.init():
                print("Repository already exists")
                return
        elif args.command == "add":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return
            
            for path in args.paths:
                repo.addPath(path)
        elif args.command == "commit":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return
            author = args.author or "User"
            repo.commit(args.message, author)
        elif args.command == "checkout":
            if not repo.gitDir.exists():
                print("Not a git repository")
                return
            repo.checkout(args.branch, args.createBranch)
            
                
    except Exception as e:
        print(f"Error: {e}" )
        sys.exit(1)
        
        
if __name__ == "__main__":
    main()