"""V3 prosody-aware streaming text chunker for Riko TTS.

This is a behavior-preserving extraction of the existing chunking algorithm.
It has no runtime state: callers retain each response turn's text buffer.
"""

import re


def clean_for_speech(text):
    # Remove Unicode emoji/symbol characters before TTS.
    text = re.sub(
        r"[\U0001F000-\U0001FAFF"
        r"\u2600-\u27BF"
        r"\uFE0F"
        r"\u200D]+",
        " ",
        text,
    )
    # Remove thinking blocks if any model output ever contains them.
    text = re.sub(r"<think>.*?</think>", " ", text, flags=re.S | re.I)

    # Remove markdown code.
    text = re.sub(r"```.*?```", " ", text, flags=re.S)

    # Remove markdown formatting.
    text = re.sub(r"[*_~`#]", "", text)

    # Remove markdown links while preserving visible text.
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # Remove bullets.
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)

    # Remove table pipes.
    text = text.replace("|", " ")

    # Remove URLs.
    text = re.sub(r"https?://\S+", " ", text)

    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text).strip()

    return text


def is_speakable(text):
    """
    Don't send emoji-only / punctuation-only chunks to GPT-SoVITS.
    """
    return bool(re.search(r"[A-Za-z0-9\u00C0-\uFFFF]", text))


def extract_chunks(buffer, final=False):
    """
    V3 — PROSODY-AWARE REALTIME TTS CHUNKER

    Goal:
        Keep GPT-SoVITS requests large enough for good throughput,
        while avoiding grammatically broken speech boundaries.

    Target:
        ~5 seconds of speech

    Soft range:
        ~3–7 seconds

    Boundary priority:
        1. Complete sentence
        2. Strong clause boundary (; :)
        3. Comma / conversational clause
        4. Hard word boundary only as a last resort

    Important:
        A target-sized buffer is NOT automatically emitted.
        We wait for a linguistically safe boundary whenever possible.
    """

    buffer = buffer.strip()

    if not buffer:
        return "", []

    WORDS_PER_SECOND = 2.5

    TARGET_SECONDS = 5.0
    MIN_SECONDS = 3.0
    MAX_SECONDS = 7.0

    TARGET_WORDS = round(TARGET_SECONDS * WORDS_PER_SECOND)
    MIN_WORDS = round(MIN_SECONDS * WORDS_PER_SECOND)
    MAX_WORDS = round(MAX_SECONDS * WORDS_PER_SECOND)

    # Words that normally indicate the thought is continuing.
    DANGLING_WORDS = {
        "and", "but", "or", "nor",
        "because", "if", "when", "while",
        "although", "though", "unless",
        "until", "since", "as",
        "than", "that", "which", "who",
        "where", "to", "of", "for",
        "from", "with", "without",
        "into", "onto", "in", "on", "at",
        "by", "about", "through",
        "like", "so", "yet",
    }

    # Conversational endings which often continue in the next token.
    SOFT_CONTINUATIONS = {
        "just", "really", "actually", "basically",
        "literally", "kind", "sort",
        "pretty", "very", "even", "still",
        "almost", "already",
    }

    def word_count(text):
        return len(text.split())

    def clean(text):
        text = clean_for_speech(text.strip())
        return text if text and is_speakable(text) else ""

    def is_dangling(text):
        """
        True when cutting here would very likely create an
        unnatural grammatical/prosodic break.
        """
        t = text.strip()

        if not t:
            return True

        # Explicit continuation punctuation.
        if t.endswith(("...", "…", "—", "–", "-", ",", ";", ":")):
            return True

        words_only = re.findall(
            r"[A-Za-zÀ-ÿ']+",
            t.lower(),
        )

        if not words_only:
            return False

        last = words_only[-1]

        if last in DANGLING_WORDS:
            return True

        if last in SOFT_CONTINUATIONS:
            return True

        # Common conversational constructions.
        if re.search(
            r"\b(?:are you just|did you just|"
            r"what if you|if you|when you|"
            r"trying to|want to|going to|"
            r"able to|one of|part of|"
            r"kind of|sort of)\s*$",
            t,
            re.IGNORECASE,
        ):
            return True

        return False

    def boundary_strength(token):
        """
        Higher score = safer place to end speech.
        """
        token = token.rstrip()

        if re.search(r'[.!?]["\')\]]*$', token):
            return 4

        if re.search(r'[;:]["\')\]]*$', token):
            return 3

        if re.search(r'[,]["\')\]]*$', token):
            return 2

        return 0

    def safe_cut(tokens, desired, minimum, maximum):
        """
        Find the best linguistically safe cut near `desired`.

        We prefer:
          - sentence endings
          - clause endings
          - commas

        We reject dangling endings.
        """

        if not tokens:
            return None

        upper = min(maximum, len(tokens))
        lower = min(minimum, upper)

        # Search around target.
        candidates = []

        for cut in range(lower, upper + 1):

            piece = " ".join(tokens[:cut]).strip()

            if not piece:
                continue

            if is_dangling(piece):
                continue

            strength = boundary_strength(tokens[cut - 1])

            if strength:
                distance = abs(cut - desired)

                # Strong boundaries are worth more than tiny
                # differences in distance from the target.
                score = (
                    strength * 100
                    - distance
                )

                candidates.append(
                    (score, cut)
                )

        if candidates:
            candidates.sort(
                key=lambda x: x[0],
                reverse=True,
            )
            return candidates[0][1]

        return None

    chunks = []

    while True:

        # ========================================================
        # 1. COMPLETE SENTENCE AVAILABLE
        # ========================================================

        sentence_match = re.search(
            r"^(.+?[.!?。！？])(?:\s+|$)",
            buffer,
            re.DOTALL,
        )

        if sentence_match:

            sentence = sentence_match.group(1).strip()
            remainder = buffer[
                sentence_match.end():
            ].lstrip()

            n = word_count(sentence)

            # ----------------------------------------------------
            # Perfect sentence size.
            # ----------------------------------------------------

            if MIN_WORDS <= n <= MAX_WORDS:

                piece = clean(sentence)

                if piece:
                    chunks.append(piece)

                buffer = remainder

                if not buffer:
                    break

                continue

            # ----------------------------------------------------
            # Short sentence.
            #
            # Prefer merging with the next complete sentence when
            # doing so stays inside our hard maximum.
            # ----------------------------------------------------

            if n < MIN_WORDS and remainder:

                next_sentence = re.match(
                    r"^(.+?[.!?。！？])(?:\s+|$)",
                    remainder,
                    re.DOTALL,
                )

                if next_sentence:

                    second = (
                        next_sentence.group(1)
                        .strip()
                    )

                    combined = (
                        sentence
                        + " "
                        + second
                    )

                    if (
                        word_count(combined)
                        <= MAX_WORDS
                    ):

                        piece = clean(combined)

                        if piece:
                            chunks.append(piece)

                        buffer = remainder[
                            next_sentence.end():
                        ].lstrip()

                        continue

            # ----------------------------------------------------
            # Long sentence.
            #
            # Split near target, but ONLY at a clause boundary
            # whenever possible.
            # ----------------------------------------------------

            if n > MAX_WORDS:

                sentence_words = sentence.split()

                cut = safe_cut(
                    sentence_words,
                    TARGET_WORDS,
                    MIN_WORDS,
                    MAX_WORDS,
                )

                if cut is None:

                    # Last resort: search a wider range for a
                    # grammatically non-dangling word boundary.
                    cut = None

                    for distance in range(
                        0,
                        MAX_WORDS,
                    ):

                        for candidate in (
                            TARGET_WORDS - distance,
                            TARGET_WORDS + distance,
                        ):

                            if (
                                MIN_WORDS
                                <= candidate
                                <= MAX_WORDS
                                and candidate
                                <= len(sentence_words)
                            ):

                                piece_test = " ".join(
                                    sentence_words[:candidate]
                                )

                                if not is_dangling(
                                    piece_test
                                ):
                                    cut = candidate
                                    break

                        if cut is not None:
                            break

                if cut is None:
                    cut = min(
                        TARGET_WORDS,
                        len(sentence_words),
                    )

                piece = " ".join(
                    sentence_words[:cut]
                )

                remaining = " ".join(
                    sentence_words[cut:]
                )

                buffer = remaining

                if remainder:
                    buffer = (
                        buffer + " " + remainder
                        if buffer
                        else remainder
                    )

                piece = clean(piece)

                if piece:
                    chunks.append(piece)

                continue

            # ----------------------------------------------------
            # Short/medium sentence that could not be merged.
            # ----------------------------------------------------

            piece = clean(sentence)

            if piece:
                chunks.append(piece)

            buffer = remainder

            if not buffer:
                break

            continue

        # ========================================================
        # 2. NO COMPLETE SENTENCE YET
        #
        # Qwen is still streaming.
        # Do NOT cut simply because target size is reached.
        # Wait until MAX and search for a safe clause boundary.
        # ========================================================

        current_words = buffer.split()

        if len(current_words) >= MAX_WORDS:

            cut = safe_cut(
                current_words,
                TARGET_WORDS,
                MIN_WORDS,
                MAX_WORDS,
            )

            # If no punctuation boundary exists, use the nearest
            # non-dangling word boundary.
            if cut is None:

                for distance in range(
                    0,
                    MAX_WORDS,
                ):

                    found = None

                    for candidate in (
                        TARGET_WORDS - distance,
                        TARGET_WORDS + distance,
                    ):

                        if (
                            MIN_WORDS
                            <= candidate
                            <= MAX_WORDS
                            and candidate
                            <= len(current_words)
                        ):

                            candidate_text = (
                                " ".join(
                                    current_words[:candidate]
                                )
                            )

                            if not is_dangling(
                                candidate_text
                            ):
                                found = candidate
                                break

                    if found is not None:
                        cut = found
                        break

            # Absolute safety valve.
            if cut is None:
                cut = TARGET_WORDS

            piece = " ".join(
                current_words[:cut]
            )

            buffer = " ".join(
                current_words[cut:]
            )

            piece = clean(piece)

            if piece:
                chunks.append(piece)

            continue

        # Not enough information yet.
        break

    # ============================================================
    # FINAL FLUSH
    # ============================================================

    if final and buffer.strip():

        remaining = buffer.split()

        while len(remaining) > MAX_WORDS:

            cut = safe_cut(
                remaining,
                TARGET_WORDS,
                MIN_WORDS,
                MAX_WORDS,
            )

            if cut is None:
                cut = TARGET_WORDS

            piece = " ".join(
                remaining[:cut]
            )

            remaining = remaining[cut:]

            piece = clean(piece)

            if piece:
                chunks.append(piece)

        if remaining:

            piece = clean(
                " ".join(remaining)
            )

            if piece:
                chunks.append(piece)

        buffer = ""

    return buffer, chunks
