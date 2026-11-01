"""Fixed limits for recipe import; every source is untrusted input."""

from __future__ import annotations

# Imports per user per UTC day (extract calls and queued jobs).
DAILY_IMPORTS_PER_USER = 30
# Longest synchronous import, and the minimum attempt deadline of a
# recipe_import job: a video may be downloaded and transcribed first.
IMPORT_DEADLINE_SECONDS = 120.0

MAX_RECIPE_URL_LENGTH = 2048
MIN_RECIPE_TEXT_LENGTH = 20
MAX_RECIPE_TEXT_LENGTH = 20_000

# Text handed to the AI, per kind of signal.
MAX_PAGE_TEXT_CHARS = 14_000
MAX_CAPTION_CHARS = 6_000
MAX_SUBTITLE_CHARS = 14_000
MAX_TRANSCRIPT_CHARS = 14_000
MAX_ON_SCREEN_TEXT_CHARS = 4_000
MAX_STRUCTURED_LINE_CHARS = 600

# Shape of a recipe draft.
MAX_INGREDIENTS = 80
MAX_STEPS = 60
MAX_NAME_CHARS = 200
MAX_DESCRIPTION_CHARS = 600
MAX_INGREDIENT_NAME_CHARS = 200
MAX_UNIT_CHARS = 40
MAX_NOTE_CHARS = 200
MAX_GROUP_CHARS = 80
MAX_STEP_CHARS = 1_500
MAX_LABEL_CHARS = 80
MAX_EMOJI_CHARS = 8

MIN_SERVINGS = 1
MAX_SERVINGS = 100
MAX_MINUTES = 2_880
MAX_STEP_MINUTES = 1_440
MAX_INGREDIENT_GRAMS = 20_000.0
MAX_INGREDIENT_QUANTITY = 100_000.0

# A caption is good enough when the recipe it yields has this much in it;
# otherwise the video itself is read (speech and on-screen text).
MIN_INGREDIENTS_FOR_CAPTION = 3
MIN_QUANTIFIED_SHARE_FOR_CAPTION = 0.5
