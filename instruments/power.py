"""The power grid. A name that exists, for a report that already did.

  python power.py                 # every power NET, and what is flagged
  python power.py --json          # the raw reply
  python power.py --near 120 140 30
  python power.py --all           # other factions' nets too

This is `buildings.py --power` under the name a session reached for and did not
find (2026-09-07). Every flag `buildings.py` takes works here; `--power` is
added for you and passing it again is harmless.

What the table says, and what it does not:

  * **capacity** is what the generators and consumers on a net COULD do -- read
    off their defs. **now** is this tick. An unfuelled generator is in the first
    and not the second, so a net can read `capacity gen 5400W / now gen 0W`.
  * a **solar flare** switches every powered building on the map off while the
    generators keep their capacity. The table says so at the top when one is
    running; without that line, `gen 5400W` above eleven dark lamps reads as
    though power exists.
"""
import sys

import buildings


def main():
    argv = list(sys.argv[1:])
    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0
    if "--power" not in argv:
        argv.append("--power")
    sys.argv = [sys.argv[0]] + argv
    return buildings.main()


if __name__ == "__main__":
    sys.exit(main())
