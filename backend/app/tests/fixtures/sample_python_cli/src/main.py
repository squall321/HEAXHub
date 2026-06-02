"""Toy entry point used in static_analyzer fixture tests."""
import os
import sys


def main() -> int:
    foo = os.environ.get("FOO", "default")
    api_key = os.environ["API_KEY"]
    name = os.getenv("USER_NAME", "world")
    print(f"hello {name}, foo={foo}, api_key={'set' if api_key else 'missing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
