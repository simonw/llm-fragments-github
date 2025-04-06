import llm
import tempfile
import subprocess
import shutil
import pathlib
from typing import List

@llm.hookimpl
def register_fragment_loaders(register):
    register("github", github_loader)


def github_loader(argument: str) -> List[llm.FragmentString]:
    """
    Load files from a GitHub repository as fragments.
    
    Args:
        argument: GitHub repository URL or username/repository format
    
    Returns:
        List of FragmentString objects, one for each file in the repository
    """
    # Normalize the repository argument
    if not argument.startswith(("http://", "https://")):
        # Assume format is username/repo
        repo_url = f"https://github.com/{argument}.git"
    else:
        repo_url = argument
        if not repo_url.endswith(".git"):
            repo_url = f"{repo_url}.git"
    
    # Create a temporary directory to clone the repository
    with tempfile.TemporaryDirectory() as temp_dir:
        try:
            # Clone the repository
            subprocess.run(
                ["git", "clone", "--depth=1", repo_url, temp_dir],
                check=True,
                capture_output=True,
                text=True
            )
            
            # Process the cloned repository
            repo_path = pathlib.Path(temp_dir)
            fragments = []
            
            # Walk through all files in the repository
            for file_path in repo_path.glob("**/*"):
                if file_path.is_file():
                    try:
                        # Try to read the file as UTF-8
                        content = file_path.read_text(encoding="utf-8")
                        
                        # Create a relative path for the fragment identifier
                        relative_path = file_path.relative_to(repo_path)
                        
                        # Add the file as a fragment
                        fragments.append(
                            llm.FragmentString(content, f"{argument}/{relative_path}")
                        )
                    except UnicodeDecodeError:
                        # Skip files that can't be decoded as UTF-8
                        continue
            
            return fragments
        except subprocess.CalledProcessError as e:
            # Handle Git errors
            raise ValueError(f"Failed to clone repository {repo_url}: {e.stderr}")
        except Exception as e:
            # Handle other errors
            raise ValueError(f"Error processing repository {repo_url}: {str(e)}")
