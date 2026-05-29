# prompt_variants.py
# Three optimized jianpu VLM prompts targeting identified failure patterns

# === BASELINE (Original Prompt) ===
PROMPT_BASELINE = (
    "Transcribe the jianpu in the image to ONE compact JSON (no spaces or newlines).\n"
    "STRUCTURE: \"measures\" is a list of MEASURES. Each measure is a list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[{...},{...}],[{...}]]            ← outer=measures, inner=notes\n"
    "  WRONG:    \"measures\":[{...},{...}]                      ← flat (no measure grouping)\n"
    "  WRONG:    \"measures\":[{\"notes\":[...]},{\"notes\":[...]}]   ← do NOT wrap notes in objects\n"
    "  WRONG:    \"measures\":[[{\"notes\":[...]}]]                ← do NOT add 'notes' key at all\n"
    "Example for 2 measures:\n"
    '{"time_signature":"4/4","key":"C","tempo":120,"measures":['
    '[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0},'
    '{"p":"1","oct":0,"dur":"h","dots":0}],'
    '[{"p":"r","oct":0,"dur":"q","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0},'
    '{"p":"7","oct":-1,"dur":"e","dots":1}]'
    "]}\n"
    "Note fields:\n"
    '- p: "1"-"7" digit; "0" → use "r" (rest). Accidentals: prefix "#" (sharp), "b" (flat).\n'
    '       Examples: "#5", "b3". A "1" with dot ABOVE may render as "i"/"í" — that is\n'
    '       ONE note = {"p":"1","oct":1}. Never split "i" into "1"+"í"; "i i" = 2 notes only.\n'
    '- oct: dot(s) ABOVE = +1 (or +2 for two dots), BELOW = -1 (or -2), none = 0.\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth(1 underline below)/16th(2 underlines).\n'
    '       A "-" after a note extends duration. "5 -" = half, "5 - -" = dotted half, "5 - - -" = whole.\n'
    '- dots: 1 if "." follows digit (dotted, +50%), else 0.\n'
    "Header:\n"
    "- Key: '1=X' (e.g. '1=A' → \"A\"). Absent → \"C\".\n"
    "- Time: stacked fraction; read BOTH numerator digits (e.g. 12/8 has 1 AND 2 on top).\n"
    "Rules:\n"
    "- '|' separates measures. Final '||' = end.\n"
    "- Default duration is eighth ('e') in 6/8 or 12/8; quarter ('q') in 3/4 or 4/4.\n"
    "- Read every digit left-to-right; ignore lyrics, measure-number superscripts, "
    "  fingering marks, breath marks, and any non-digit text.\n"
    "- After the LAST measure write ']}' and STOP."
)

# === VARIANT A: CRITICAL - Digit-to-Note Pitch Mapping (Addresses 91.7% pitch error) ===
PROMPT_VARIANT_A = (
    "CRITICAL: You are transcribing JIANPU (numbered notation), NOT staff notation.\n"
    "In JIANPU, the digits 1-7 ALWAYS represent a fixed scale independent of the key.\n"
    "These digits are:\n"
    "  1=DO  2=RE  3=MI  4=FA  5=SOL  6=LA  7=TI\n"
    "\n"
    "KEY SIGNATURE EFFECT: The key signature (e.g., '1=C' or '1=A') shifts WHICH NOTE\n"
    "each digit corresponds to in Western notation. But the JIANPU DIGITS THEMSELVES NEVER CHANGE.\n"
    "You must ALWAYS read a digit '1' as \"1\", '2' as \"2\", etc., in your JSON output.\n"
    "NEVER try to transpose the digit values based on key signature.\n"
    "\n"
    "DIGIT REFERENCE CHART (what you see visually):\n"
    "  Character '1' on the page → output {\"p\":\"1\"}\n"
    "  Character '2' on the page → output {\"p\":\"2\"}\n"
    "  Character '3' on the page → output {\"p\":\"3\"}\n"
    "  Character '4' on the page → output {\"p\":\"4\"}\n"
    "  Character '5' on the page → output {\"p\":\"5\"}\n"
    "  Character '6' on the page → output {\"p\":\"6\"}\n"
    "  Character '7' on the page → output {\"p\":\"7\"}\n"
    "\n"
    "OCTAVE MARKERS:\n"
    "- No dot/marker above/below the digit → octave 0 (middle)\n"
    "- One dot ABOVE the digit (or 'i'/'í') → octave +1 (high)\n"
    "- Two dots ABOVE → octave +2\n"
    "- One dot BELOW → octave -1 (low)\n"
    "- Two dots BELOW → octave -2\n"
    "\n"
    "Transcribe the jianpu in the image to ONE compact JSON (no spaces or newlines).\n"
    "STRUCTURE: \"measures\" is a list of MEASURES. Each measure is a list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[{...},{...}],[{...}]]            ← outer=measures, inner=notes\n"
    "  WRONG:    \"measures\":[{...},{...}]                      ← flat (no measure grouping)\n"
    "  WRONG:    \"measures\":[{\"notes\":[...]},{\"notes\":[...]}]   ← do NOT wrap notes in objects\n"
    "  WRONG:    \"measures\":[[{\"notes\":[...]}]]                ← do NOT add 'notes' key at all\n"
    "\n"
    "Example for key='C', 2 measures (1=C means digit 1→C, but you output 1 not C):\n"
    '{"time_signature":"4/4","key":"C","tempo":120,"measures":['
    '[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0},'
    '{"p":"1","oct":0,"dur":"h","dots":0}],'
    '[{"p":"r","oct":0,"dur":"q","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0},'
    '{"p":"7","oct":-1,"dur":"e","dots":1}]'
    "]}\n"
    "\n"
    "Note fields:\n"
    '- p: "1"-"7" digit EXACTLY AS YOU SEE IT (not transposed by key); "0" → use "r" (rest).\n'
    '       Accidentals: prefix "#" (sharp), "b" (flat). Examples: "#5", "b3".\n'
    '       A "1" with dot ABOVE may render as "i"/"í" — that is ONE note = {"p":"1","oct":1}.\n'
    '- oct: +1 if dots ABOVE, -1 if BELOW, 0 if none. (Count dots: +N or -N for multiple)\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth/16th.\n'
    '       A "-" after a note extends duration. "5 -" = half, "5 - -" = dotted half, "5 - - -" = whole.\n'
    '- dots: 1 if "." follows digit, else 0.\n'
    "\n"
    "Header:\n"
    "- Key: '1=X' means digit 1 = note X. Output as {\"key\":\"X\"}. Absent → \"C\".\n"
    "- Time: stacked fraction; read BOTH numerator digits.\n"
    "\n"
    "Rules:\n"
    "- '|' separates measures. Final '||' = end.\n"
    "- Default duration is eighth ('e') in 6/8 or 12/8; quarter ('q') in 3/4 or 4/4.\n"
    "- Read every digit left-to-right; ignore lyrics, measure-number superscripts, "
    "  fingering marks, breath marks, and any non-digit text.\n"
    "- After the LAST measure write ']}' and STOP."
)

# === VARIANT B: HIGH - Duration Notation & Octave Markers (Addresses 63.9% duration + 38.9% octave) ===
PROMPT_VARIANT_B = (
    "Transcribe the jianpu in the image to ONE compact JSON (no spaces or newlines).\n"
    "\n"
    "DIGIT-TO-JIANPU-SCALE (critical to read digits exactly as written):\n"
    "  1=DO  2=RE  3=MI  4=FA  5=SOL  6=LA  7=TI\n"
    "Output digit value 1-7 EXACTLY AS YOU SEE THEM on the page. Never shift digits by key.\n"
    "\n"
    "DURATION NOTATION IN JIANPU:\n"
    "  - No underline = quarter note ('q')  [in 4/4]\n"
    "  - ONE underline BELOW digit = eighth note ('e')  [one stem flag]\n"
    "  - TWO underlines BELOW digit = sixteenth note ('s')  [two stem flags]\n"
    "  - ABOVE digit (or dash-like mark): \"-\" extends the duration:\n"
    "    * '5' = quarter;  '5 -' = half (extends by one quarter) \n"
    "    * '5 - -' = dotted half (quarter + 2 quarters)\n"
    "    * '5 - - -' = whole note (4 quarters)\n"
    "  IMPORTANT: Do NOT confuse duration dashes with accidentals or dots.\n"
    "             Dashes are written SEPARATELY from the digit (usually to the right or below).\n"
    "\n"
    "OCTAVE POSITION MARKERS (small dots or diacritics on/near digit):\n"
    "  CRITICAL: These are often tiny visual details. Look carefully for:\n"
    "    - Dot DIRECTLY ABOVE digit: octave +1 (high octave)\n"
    "    - Two dots above: octave +2\n"
    "    - Dot DIRECTLY BELOW digit: octave -1 (low octave)\n"
    "    - Two dots below: octave -2\n"
    "    - NO dot/marker: octave 0 (middle)\n"
    "  Examples:\n"
    "    '1' alone = {\"p\":\"1\",\"oct\":0}\n"
    "    '1' with dot above = {\"p\":\"1\",\"oct\":1}  (also may render as 'i')\n"
    "    '1' with dot below = {\"p\":\"1\",\"oct\":-1}\n"
    "\n"
    "Transcribe to ONE compact JSON (no spaces or newlines).\n"
    "STRUCTURE: \"measures\" is a list of MEASURES. Each measure is a list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[{...},{...}],[{...}]]            ← outer=measures, inner=notes\n"
    "  WRONG:    \"measures\":[{...},{...}]                      ← flat (no measure grouping)\n"
    "  WRONG:    \"measures\":[{\"notes\":[...]},{\"notes\":[...]}]   ← do NOT wrap notes in objects\n"
    "Example for 2 measures:\n"
    '{"time_signature":"4/4","key":"C","tempo":120,"measures":['
    '[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0},'
    '{"p":"1","oct":0,"dur":"h","dots":0}],'
    '[{"p":"r","oct":0,"dur":"q","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0},'
    '{"p":"7","oct":-1,"dur":"e","dots":1}]'
    "]}\n"
    "\n"
    "Note fields:\n"
    '- p: "1"-"7" digit EXACTLY AS WRITTEN. "0" → "r" (rest). Accidentals: "#"/"b".\n'
    '- oct: +1/-1 (±2 for double dots). READ THE VISUAL DOTS CAREFULLY.\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth/sixteenth. Count UNDERLINES BELOW.\n'
    '       Dashes ("-") extend: "5 -" = half, "5 - -" = dotted half, "5 - - -" = whole.\n'
    '- dots: 1 if literal "." after digit, else 0.\n'
    "\n"
    "Header:\n"
    "- Key: Extract from '1=X' pattern. Absent → \"C\".\n"
    "- Time: Read stacked fraction (both numerator digits).\n"
    "\n"
    "Rules:\n"
    "- '|' = measure separator. '||' = end.\n"
    "- Default: 'e' in 6/8/12/8; 'q' in 3/4/4/4.\n"
    "- Ignore lyrics, superscripts, fingerings, breath marks.\n"
    "- After LAST measure, write ']}' and STOP."
)

# === VARIANT C: INTEGRATION - All three fixes together ===
PROMPT_VARIANT_C = (
    "You are a jianpu OCR engine. Output ONE compact JSON object describing the whole score.\n"
    "CRITICAL RULES:\n"
    "\n"
    "1. DIGIT PITCH RECOGNITION (do NOT transpose by key signature):\n"
    "   Jianpu uses digits 1-7 to represent a FIXED scale (DO-RE-MI-FA-SOL-LA-TI).\n"
    "   The key signature only tells you what note 1=DO corresponds to in Western notation.\n"
    "   You ALWAYS output digits 1-7 exactly as you SEE them on the page.\n"
    "   \n"
    "   DIGIT CHART:\n"
    "   If you see:  '1' → output {\"p\":\"1\"}  (regardless of key)\n"
    "   If you see:  '2' → output {\"p\":\"2\"}  (regardless of key)\n"
    "   ... and so on for 3,4,5,6,7\n"
    "   \n"
    "   Common mistake: Reading '3' as '2' because it LOOKS like another digit.\n"
    "   Solution: Compare each digit carefully to the reference chart above.\n"
    "\n"
    "2. DURATION NOTATION (count UNDERLINES and DASHES carefully):\n"
    "   Baseline (no marks) = quarter ('q')  [4/4 time]\n"
    "   ONE underline below = eighth ('e')   [exactly 1 flag on stem]\n"
    "   TWO underlines = sixteenth ('s')     [exactly 2 flags]\n"
    "   \n"
    "   Duration EXTENSION marks (dashes, NOT dots):\n"
    "   - '5 -' (dash after 5) = half note (extends quarter by 1 quarter)\n"
    "   - '5 - -' (two dashes) = dotted half (3 quarters)\n"
    "   - '5 - - -' (three dashes) = whole (4 quarters)\n"
    "   \n"
    "   Do NOT confuse:\n"
    "   - Dots (.) AFTER digit = dotted rhythm (1.5× duration), NOT duration marks\n"
    "   - Dashes (-) = duration extension (adds to length)\n"
    "\n"
    "3. OCTAVE MARKERS (tiny dots above/below digit — easy to miss!):\n"
    "   These are CRITICAL for high/low notes.\n"
    "   - Dot ABOVE digit = octave +1   [high octave]\n"
    "   - Two dots above = octave +2    [very high]\n"
    "   - Dot BELOW digit = octave -1   [low octave]\n"
    "   - Two dots below = octave -2    [very low]\n"
    "   - NO dot = octave 0             [middle, normal]\n"
    "   \n"
    "   Special: '1' with dot above may render as 'i' or 'í' — treat as {\"p\":\"1\",\"oct\":1}\n"
    "\n"
    "Transcribe to ONE compact JSON (no spaces or newlines).\n"
    "STRUCTURE: \"measures\" is list of MEASURES, each is list of NOTE objects.\n"
    "  CORRECT:  \"measures\":[[note,note],[note,note]]\n"
    "  WRONG:    \"measures\":[note,note] (flat, missing inner [])\n"
    "\n"
    "Example for key='C' (1=DO=C), 2 measures in 4/4:\n"
    '{"time_signature":"4/4","key":"C","tempo":120,"measures":['
    '[{"p":"5","oct":0,"dur":"q","dots":0},{"p":"3","oct":0,"dur":"q","dots":0}],'
    '[{"p":"1","oct":0,"dur":"h","dots":0},{"p":"6","oct":-1,"dur":"e","dots":0}]'
    "]}\n"
    "\n"
    "Note object fields:\n"
    '- p: "1"-"7" (jianpu digit as written). "0" → "r" (rest). Prefix: "#"=sharp, "b"=flat.\n'
    '- oct: +1/-1 (or ±2). LOOK for dots above/below digit. 0 = no marker.\n'
    '- dur: w/h/q/e/s = whole/half/quarter/eighth/sixteenth.\n'
    '       Count underlines: none="q", 1="e", 2="s".\n'
    '       Dashes extend: "5 -" = half, "5 - -" = dotted-half, "5 - - -" = whole.\n'
    '- dots: 1 if "." after digit (dotted rhythm), else 0.\n'
    "\n"
    "Header fields:\n"
    "- Key: Extract '1=X' from image. If absent, use \"C\". Output as {\"key\":\"X\"}.\n"
    "- Time_signature: Stacked fraction. Read BOTH numerator digits (e.g., 12/8 → \"12/8\").\n"
    "- Tempo: Usually tempo marking on page. If absent, use 120.\n"
    "\n"
    "Rules:\n"
    "- '|' = measure bar. '||' or final bar = end.\n"
    "- Default duration: 'q' in 4/4/3/4; 'e' in 6/8/12/8.\n"
    "- Ignore: lyrics, measure numbers, fingerings, breath marks, any non-digit text.\n"
    "- Do NOT skip notes. Read left-to-right, top-to-bottom.\n"
    "- After LAST measure write ']}' and STOP. No extra text."
)
