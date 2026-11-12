"""Frozen entry point used by the Linux AppImage build."""

from mizuki.main import main

raise SystemExit(main())
