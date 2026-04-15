from __future__ import annotations
import argparse
from pathlib import Path
import hashlib
import zlib
import sys
from typing import Dict, List, Optional, Tuple

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
        header = decompressed[:nullIdx]
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
                
            
    def createTreeFromIndex(self):
        index = self.loadIndex()
        if not index:
            tree = Tree()
            return self.storeObject(tree)
            
        pass
    
    def commit(self, message: str, author: str = "User"):
        # Create a tree object from the index
        treeHash = self.createTreeFromIndex()
        
        pass
    
        
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
                
    except Exception as e:
        print(f"Error: {e}" )
        sys.exit(1)
        
        
if __name__ == "__main__":
    main()