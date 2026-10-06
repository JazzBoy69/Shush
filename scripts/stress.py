"""Bounded phrase/lexical stress rules for canonical English appliance prompts.

No model imports. Unknown text/context or unexpected phoneme forms fail closed.
Only primary/secondary stress markers may change; speech sounds are preserved.
"""

import re

PASS_VERSION = "shush-phrase-stress-v7"
STRESS_MARKERS = "ˈˌ"
NUMBER_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
NUMBER_PHONEMES = [
    "wˌʌn",
    "tˈuː",
    "θɹˈiː",
    "fˈɔːɹ",
    "fˈaɪv",
    "sˈɪks",
    "sˈɛvən",
    "ˈeɪt",
    "nˈaɪn",
    "tˈɛn",
    "ᵻlˈɛvən",
    "twˈɛlv",
    "θˈɜːtiːn",
    "fˈɔːɹtiːn",
    "fˈɪftiːn",
    "sˈɪkstiːn",
    "sˈɛvəntˌiːn",
    "ˈeɪtiːn",
    "nˈaɪntiːn",
]
WORDS = dict(zip(NUMBER_WORDS, NUMBER_PHONEMES))
WORDS.update(
    {
        "twenty": "twˈɛnti",
        "thirty": "θˈɜːɾi",
        "forty": "fˈɔːɹɾi",
        "fifty": "fˈɪfti",
        "oh": "ˈoʊ",
        "o'clock": "əklˈɑːk",
    }
)
TEEN_RULES = {
    "thirteen": ("θˈɜːtiːn", "θɜːtˈiːn"),
    "fourteen": ("fˈɔːɹtiːn", "fɔːɹtˈiːn"),
    "fifteen": ("fˈɪftiːn", "fɪftˈiːn"),
    "sixteen": ("sˈɪkstiːn", "sɪkstˈiːn"),
    "seventeen": ("sˈɛvəntˌiːn", "sɛvəntˈiːn"),
    "eighteen": ("ˈeɪtiːn", "eɪtˈiːn"),
    "nineteen": ("nˈaɪntiːn", "naɪntˈiːn"),
}
PREFIXES = {
    "Your alarm is set for ": "jʊɹ ɐlˈɑːɹm ɪz sˈɛt fɔːɹ ",
    "The current time is ": "ðə kˈɜːɹənt tˈaɪm ɪz ",
}
UNCHANGED = {
    "With reading.": "wɪð ɹˈiːdɪŋ.",
    "Playback cannot start. Check appliance diagnostics.": "plˈeɪbæk kænˈɑːt stˈɑːɹt. tʃˈɛk ɐplˈaɪəns dˌaɪəɡnˈɑːstɪks.",
}


def number(value):
    if 1 <= value <= 19:
        return NUMBER_WORDS[value - 1]
    tens, ones = divmod(value, 10)
    if not 2 <= tens <= 5:
        raise ValueError("Unsupported number")
    base = ["twenty", "thirty", "forty", "fifty"][tens - 2]
    return base + (" " + number(ones) if ones else "")


def _time_phrases():
    values = {}
    for hour in range(1, 13):
        for minute in range(60):
            suffix = (
                "o'clock"
                if minute == 0
                else ("oh " + number(minute) if minute < 10 else number(minute))
            )
            values[number(hour) + " " + suffix] = minute
    return values


TIME_PHRASES = _time_phrases()


def _expect(actual, expected):
    if actual != expected:
        raise ValueError(
            f"Unexpected audited phoneme form: expected {expected!r}, got {actual!r}"
        )


def postprocess_stress(text, phonemes):
    """Return (corrected_phonemes, correction_metadata) for canonical prompts.

    Accept the two mode/error texts and the canonical alarm/current prefixes
    followed by a 1..12 hour and 0..59 minute phrase. Introductions receive
    reduced prominence; the time or sentence-ending error word carries focus.
    Lexical stress positions are retained except audited teen corrections.
    ValueError rejects unrecognized text or phoneme drift.
    """
    corrections = []

    def change(word, role, original, replacement, rule):
        if original != replacement:
            corrections.append(
                {
                    "rule": rule,
                    "word": word,
                    "role": role,
                    "from": original,
                    "to": replacement,
                }
            )
        return replacement

    def prominence(word, role, original, level):
        if level == "unstressed":
            replacement = original.translate(str.maketrans("", "", STRESS_MARKERS))
        else:
            marker = "ˈ" if level == "primary" else "ˌ"
            replacement = re.sub("[ˈˌ]", marker, original)
        return change(word, role, original, replacement, level + "-prominence")

    if text == "With reading.":
        _expect(phonemes, UNCHANGED[text])
        corrected = (
            change(
                "with", "mode-focus", "wɪð", "wˈɪð", "contrastive-mode-primary-stress"
            )
            + " "
            + prominence("reading", "mode-complement", "ɹˈiːdɪŋ", "secondary")
            + "."
        )
    elif text == "Playback cannot start. Check appliance diagnostics.":
        _expect(phonemes, UNCHANGED[text])
        # Preserve diagnostics' internal secondary and final primary stress.
        first = [
            prominence("playback", "error-head", "plˈeɪbæk", "secondary"),
            # Neutral US CANnot (/ˈkænɑːt/), rather than emphatic canNOT.
            # Sentence focus remains on "start", so this word is secondary.
            change(
                "cannot",
                "error-head",
                "kænˈɑːt",
                "kˌænɑːt",
                "neutral-cannot-lexical-stress",
            ),
            "stˈɑːɹt",
        ]
        second = [
            prominence("check", "error-head", "tʃˈɛk", "secondary"),
            prominence("appliance", "error-head", "ɐplˈaɪəns", "secondary"),
            "dˌaɪəɡnˈɑːstɪks",
        ]
        corrected = " ".join(first) + ". " + " ".join(second) + "."
    elif text == "Without reading.":
        _expect(phonemes, "wɪðˌaʊt ɹˈiːdɪŋ.")
        corrected = (
            prominence("without", "mode-modifier", "wɪðˌaʊt", "secondary") + " ɹˈiːdɪŋ."
        )
    else:
        prefix = next((p for p in PREFIXES if text.startswith(p)), None)
        if prefix is None or not text.endswith("."):
            raise ValueError("Unsupported appliance prompt text")
        time = text[len(prefix) : -1]
        minute = TIME_PHRASES.get(time)
        if minute is None:
            raise ValueError("Unsupported appliance time phrase")
        words = time.split()
        sounds = [WORDS[word] for word in words]
        # Hour 'one' has primary stress; minute 'one' has secondary stress.
        # Validate both audited input forms before applying role prominence.
        if words[0] == "one":
            sounds[0] = "wˈʌn"
        expected = PREFIXES[prefix] + " ".join(sounds) + "."
        _expect(phonemes, expected)
        if prefix == "Your alarm is set for ":
            introduction = (
                "jʊɹ "
                + prominence("alarm", "introduction", "ɐlˈɑːɹm", "secondary")
                + " ɪz "
                + prominence("set", "introduction", "sˈɛt", "unstressed")
                + " fɔːɹ "
            )
        else:
            introduction = (
                "ðə "
                + prominence("current", "introduction", "kˈɜːɹənt", "secondary")
                + " "
                + prominence("time", "introduction", "tˈaɪm", "secondary")
                + " ɪz "
            )
        sounds[0] = prominence(
            words[0], "hour", sounds[0], "primary" if minute == 0 else "secondary"
        )
        if minute == 0:
            sounds[1] = prominence(words[1], "clock-label", sounds[1], "secondary")
        elif minute < 10:
            sounds[1] = prominence(words[1], "leading-zero", sounds[1], "unstressed")
            sounds[2] = prominence(words[2], "minute-unit", sounds[2], "primary")
        elif 13 <= minute <= 19:
            word = number(minute)
            original, replacement = TEEN_RULES[word]
            sounds[1] = change(
                word, "minute", original, replacement, "terminal-teen-primary-stress"
            )
        elif minute >= 20 and minute % 10:
            sounds[1] = prominence(words[1], "minute-tens", sounds[1], "secondary")
            sounds[2] = prominence(words[2], "minute-unit", sounds[2], "primary")
        else:
            sounds[1] = prominence(words[1], "minute", sounds[1], "primary")
        corrected = introduction + " ".join(sounds) + "."
    if re.sub("[ˈˌ]", "", phonemes) != re.sub("[ˈˌ]", "", corrected):
        raise ValueError("Stress pass changed speech sounds")
    return corrected, corrections
