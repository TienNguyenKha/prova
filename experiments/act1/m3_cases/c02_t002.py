import sys


def main():
    if len(sys.argv) == 3:
        expected_path = sys.argv[1]
        actual_path = sys.argv[2]

    elif len(sys.argv) == 3 and sys.argv[1].startswith("http"):
        url = sys.argv[1]
        expected_path = sys.argv[2]
        actual_path = capture_screenshot(url)

    else:
        print("Usage:")
        print("  python screenshot_debugger.py expected.png actual.png")
        print("  python screenshot_debugger.py http://localhost:3000 expected.png")
        sys.exit(1)

    compare_images(expected_path, actual_path)
