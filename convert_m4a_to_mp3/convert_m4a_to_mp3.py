#!/usr/bin/env python3
"""
Convert all M4A audio files in the current directory to MP3 format using ffmpeg.
"""

import os
import sys
import subprocess
import glob
import argparse
from pathlib import Path


def check_ffmpeg() -> bool:
    """Check if ffmpeg is available in system PATH."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def convert_file(input_path: Path, output_path: Path, quality: int = 2) -> bool:
    """
    Convert a single M4A file to MP3 using ffmpeg.

    Args:
        input_path: Path to input M4A file
        output_path: Path for output MP3 file
        quality: MP3 quality (0=best, 9=worst, default=2)

    Returns:
        True if conversion succeeded, False otherwise
    """
    try:
        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-i", str(input_path),
            "-codec:a", "libmp3lame",
            "-qscale:a", str(quality),
            "-y",  # Overwrite output file if exists
            str(output_path)
        ]

        print(f"Converting: {input_path.name} -> {output_path.name}")

        # Run ffmpeg
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )

        # Check if output file was created
        if output_path.exists():
            size_mb = output_path.stat().st_size / (1024 * 1024)
            print(f"  ✓ Success: {size_mb:.2f} MB")
            return True
        else:
            print(f"  ✗ Failed: Output file not created")
            return False

    except subprocess.CalledProcessError as e:
        print(f"  ✗ FFmpeg error: {e.stderr[:200] if e.stderr else str(e)}")
        return False
    except Exception as e:
        print(f"  ✗ Unexpected error: {str(e)}")
        return False


def find_m4a_files(directory: Path, recursive: bool = False) -> list[Path]:
    """
    Find all M4A files in the given directory.

    Args:
        directory: Directory to search in
        recursive: If True, search recursively in subdirectories

    Returns:
        List of Path objects for M4A files
    """
    pattern = "**/*.m4a" if recursive else "*.m4a"
    files = list(directory.glob(pattern))

    # Filter out directories (just in case)
    files = [f for f in files if f.is_file()]

    return files


def main():
    parser = argparse.ArgumentParser(
        description="Convert M4A audio files to MP3 format using ffmpeg"
    )
    parser.add_argument(
        "-r", "--recursive",
        action="store_true",
        help="Search for M4A files recursively in subdirectories"
    )
    parser.add_argument(
        "-q", "--quality",
        type=int,
        default=2,
        choices=range(0, 10),
        help="MP3 quality (0=best, 9=worst, default=2)"
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        help="Output directory for MP3 files (default: same as input)"
    )
    parser.add_argument(
        "-d", "--dry-run",
        action="store_true",
        help="Show what would be converted without actually converting"
    )

    args = parser.parse_args()

    # Check if ffmpeg is available
    if not check_ffmpeg():
        print("Error: ffmpeg is not installed or not in PATH", file=sys.stderr)
        print("Please install ffmpeg first:", file=sys.stderr)
        print("  macOS: brew install ffmpeg", file=sys.stderr)
        print("  Ubuntu/Debian: sudo apt install ffmpeg", file=sys.stderr)
        print("  Windows: download from ffmpeg.org", file=sys.stderr)
        sys.exit(1)

    # Get current directory
    current_dir = Path.cwd()

    # Find M4A files
    m4a_files = find_m4a_files(current_dir, args.recursive)

    if not m4a_files:
        print(f"No M4A files found in {current_dir}")
        if not args.recursive:
            print("Use --recursive to search in subdirectories")
        sys.exit(0)

    print(f"Found {len(m4a_files)} M4A file(s)")

    # Process each file
    successful = 0
    failed = 0

    for input_path in m4a_files:
        # Determine output path
        if args.output_dir:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            output_path = args.output_dir / f"{input_path.stem}.mp3"
        else:
            output_path = input_path.with_suffix(".mp3")

        if args.dry_run:
            print(f"[DRY RUN] Would convert: {input_path} -> {output_path}")
            successful += 1
            continue

        # Check if output already exists
        if output_path.exists():
            print(f"  ⚠ Skipping: {output_path.name} already exists")
            continue

        # Convert the file
        if convert_file(input_path, output_path, args.quality):
            successful += 1
        else:
            failed += 1

    # Print summary
    print("\n" + "="*50)
    print("Conversion Summary:")
    print(f"  Successful: {successful}")
    print(f"  Failed: {failed}")
    print(f"  Total processed: {successful + failed}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()