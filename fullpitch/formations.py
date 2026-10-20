"""Formation templates in team-relative coordinates.

Each outfield slot (1..10) has a (depth, lat) pair:
  depth: 0 = the defensive line, 1 = the forward line (negative = deeper than the line)
  lat:   lateral offset as a fraction of team width, positive = the team's left
Slot 0 is always the goalkeeper and is positioned separately.

Every formation has an "att" (in possession) and "def" (out of possession) variant,
plus the line group of each slot in both phases (D, M, A, F) for drawing team shape.
Slots keep their index across formations, so a formation change moves players
from one spot to another instead of shuffling identities.
"""

FORMATIONS = {
    "4-3-3": {
        "roles": ["GK", "LB", "LCB", "RCB", "RB", "LCM", "CM", "RCM", "LW", "ST", "RW"],
        "att": [None, (0.14, 0.46), (0.0, 0.17), (0.0, -0.17), (0.14, -0.46),
                (0.50, 0.22), (0.34, 0.0), (0.50, -0.22),
                (0.92, 0.42), (1.0, 0.0), (0.92, -0.42)],
        "def": [None, (0.0, 0.42), (0.0, 0.14), (0.0, -0.14), (0.0, -0.42),
                (0.44, 0.25), (0.38, 0.0), (0.44, -0.25),
                (0.78, 0.38), (1.0, 0.0), (0.78, -0.38)],
        "att_groups": ["G", "D", "D", "D", "D", "M", "M", "M", "F", "F", "F"],
        "def_groups": ["G", "D", "D", "D", "D", "M", "M", "M", "F", "F", "F"],
    },
    "4-2-3-1": {
        "roles": ["GK", "LB", "LCB", "RCB", "RB", "LDM", "RDM", "LAM", "CAM", "RAM", "ST"],
        "att": [None, (0.16, 0.47), (0.0, 0.17), (0.0, -0.17), (0.16, -0.47),
                (0.30, 0.13), (0.30, -0.13),
                (0.76, 0.40), (0.66, 0.0), (0.76, -0.40), (1.0, 0.0)],
        "def": [None, (0.0, 0.42), (0.0, 0.14), (0.0, -0.14), (0.0, -0.42),
                (0.30, 0.12), (0.30, -0.12),
                (0.60, 0.40), (0.66, 0.0), (0.60, -0.40), (1.0, 0.0)],
        "att_groups": ["G", "D", "D", "D", "D", "M", "M", "A", "A", "A", "F"],
        "def_groups": ["G", "D", "D", "D", "D", "M", "M", "A", "A", "A", "F"],
    },
    "3-5-2": {
        # Slot order matches the back-four formations, so full-backs become wing-backs
        # and slot 6 (a central midfielder) becomes the middle centre-back.
        "roles": ["GK", "LWB", "LCB", "RCB", "RWB", "LCM", "CB", "RCM", "CM", "LST", "RST"],
        "att": [None, (0.58, 0.48), (0.0, 0.26), (0.0, -0.26), (0.58, -0.48),
                (0.52, 0.20), (-0.04, 0.0), (0.52, -0.20),
                (0.36, 0.0), (1.0, 0.12), (1.0, -0.12)],
        "def": [None, (0.03, 0.46), (0.0, 0.23), (0.0, -0.23), (0.03, -0.46),
                (0.42, 0.22), (-0.03, 0.0), (0.42, -0.22),
                (0.38, 0.0), (0.92, 0.13), (0.92, -0.13)],
        "att_groups": ["G", "M", "D", "D", "M", "M", "D", "M", "M", "F", "F"],
        "def_groups": ["G", "D", "D", "D", "D", "M", "D", "M", "M", "F", "F"],
    },
}
