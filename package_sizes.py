import requests
import os
import sys

from pathlib import Path

def human_readable_size(size, decimal_places=2):
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024:
            return f"{size:.{decimal_places}f} {unit}"
        size /= 1024
    return f"{size:.{decimal_places}f} PB"

def get_package_size(package_name):
    url = f"https://pypi.org/pypi/{package_name}/json"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Get the latest version
            version = data.get('info', {}).get('version')
            if not version:
                print(f"Could not find the latest version for package '{package_name}'.")
                return None
            releases = data.get('releases', {}).get(version, [])
            if not releases:
                print(f"No releases found for package '{package_name}' version '{version}'.")
                return None
            # Sum the sizes of all distribution files
            total_size = 0
            for file in releases:
                file_size = file.get('size', 0)
                total_size += file_size
            return total_size
        elif response.status_code == 404:
            print(f"Package '{package_name}' not found on PyPI.")
            return None
        else:
            print(f"Failed to fetch data for package '{package_name}'. Status code: {response.status_code}")
            return None
    except requests.RequestException as e:
        print(f"Error fetching package '{package_name}': {e}")
        return None

def read_requirements(file_path):
    packages = []
    try:
        with open(file_path, 'r') as f:
            for line in f:
                # Remove comments and whitespace
                line = line.split('#')[0].strip()
                if line:
                    # Handle package specifications like package==version
                    package = line.split('==')[0].strip()
                    packages.append(package)
        return packages
    except FileNotFoundError:
        print(f"Requirements file '{file_path}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading requirements file: {e}")
        sys.exit(1)

def main():
    # Define the path to the requirements.txt
    requirements_path = Path('requirements.txt')
    if not requirements_path.exists():
        print("requirements.txt file not found in the current directory.")
        sys.exit(1)
    
    packages = read_requirements(requirements_path)
    if not packages:
        print("No packages found in requirements.txt.")
        sys.exit(0)
    
    print(f"{'Package':<30} {'Size':>10}")
    print("-" * 42)
    
    total_size = 0
    for pkg in packages:
        size = get_package_size(pkg)
        if size is not None:
            total_size += size
            print(f"{pkg:<30} {human_readable_size(size):>10}")
        else:
            print(f"{pkg:<30} {'N/A':>10}")
    
    print("-" * 42)
    print(f"{'Total':<30} {human_readable_size(total_size):>10}")

if __name__ == "__main__":
    main()
