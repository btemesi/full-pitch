"""Match scenarios: two fictional teams and a timeline of tactical decisions.

The timeline is the ground truth of what the coaches changed. It is written to
truth.json for evaluation only; the live event stream never announces it, so the
analysis layer has to discover the changes from movement, like a human would.
"""

FIRST_NAMES = [
    "Adam", "Bruno", "Callum", "Dario", "Emil", "Felix", "Gabriel", "Hugo", "Ivan",
    "Jonas", "Kai", "Luca", "Mateo", "Nils", "Oscar", "Pablo", "Quentin", "Rafael",
    "Samir", "Theo", "Umar", "Viktor", "Wes", "Xavi", "Yannick", "Zoltan", "Arlo",
    "Bastian", "Cyril", "Dominik", "Elias", "Florin",
]
LAST_NAMES = [
    "Ashby", "Brannigan", "Castell", "Dorsey", "Ekwueme", "Falk", "Garrow", "Holm",
    "Ibarra", "Jessop", "Kovacic", "Lindqvist", "Marlowe", "Novak", "Okafor", "Pell",
    "Quarry", "Renshaw", "Sauer", "Tavernier", "Ulloa", "Varga", "Whitlock", "Yates",
    "Zelenko", "Abara", "Bellweather", "Crane", "Delacroix", "Esterhuizen", "Fennick",
    "Grieve",
]

# Base params: lines are metres from the team's own goal line.
#   def_line / att_line : highest the back line will sit without / with the ball
#   length_* / width_*  : distance back line -> forward line, and team width (m)
#   press   : 0..1 how eagerly and with how many players they press the ball
#   tempo   : 0..1 how quickly and how vertically they move the ball
#   quality : passing reliability, finish: finishing multiplier
#   channel : -1..1 preferred attacking side (+ = their left)
DEFAULT_SCENARIO = {
    "match_id": "FP-0001",
    "competition": "Synthetic League",
    "venue": "Harbour Park",
    "stoppage": [120, 240],
    "teams": [
        {
            "name": "Riverside Athletic", "short": "RIV", "color": "#d94a3d", "alt": "#ffffff",
            "formation": "4-3-3",
            "params": {"def_line": 41, "att_line": 54, "length_def": 33, "length_att": 47,
                       "width_def": 40, "width_att": 62, "press": 0.8, "tempo": 0.65,
                       "quality": 0.9, "finish": 1.0, "channel": 0.15},
        },
        {
            "name": "Northgate FC", "short": "NOR", "color": "#2f6fd6", "alt": "#ffffff",
            "formation": "4-2-3-1",
            "params": {"def_line": 33, "att_line": 50, "length_def": 30, "length_att": 45,
                       "width_def": 38, "width_att": 60, "press": 0.5, "tempo": 0.55,
                       "quality": 0.9, "finish": 1.0, "channel": 0.0},
        },
    ],
    "timeline": [
        {"t": 31 * 60, "team": 1, "kind": "block",
         "label": "Northgate drop into a deep low block and stop pressing",
         "set": {"def_line": 23, "length_def": 27, "width_def": 35, "press": 0.25}},
        {"t": 45 * 60 + 9 * 60, "team": 1, "kind": "formation",
         "label": "Northgate switch to a back three (3-5-2) and attack down their left",
         "formation": "3-5-2", "subs": [{"slot": 6, "role": "CB"}],
         "set": {"def_line": 31, "length_def": 30, "width_def": 40, "press": 0.55,
                 "tempo": 0.7, "channel": 0.7}},
        {"t": 45 * 60 + 24 * 60, "team": 0, "kind": "press",
         "label": "Riverside push their line higher and press all-out",
         "subs": [{"slot": 6}, {"slot": 9}],
         "set": {"def_line": 48, "att_line": 60, "press": 0.97, "tempo": 0.85,
                 "length_def": 36}},
    ],
}
