#!/usr/bin/env python3
from context_lens.overlay import main

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        import sys
        print(str(exc), file=sys.stderr)
        sys.exit(1)
