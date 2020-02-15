# Originally from: https://github.com/trezor/python-mnemonic
#
# Copyright (c) 2013 Pavol Rusnak
# Copyright (c) 2017 mruddy
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
# of the Software, and to permit persons to whom the Software is furnished to do
# so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

import binascii
import hashlib
import os
from pathlib import (
    Path,
)
import unicodedata

from eth_utils import (
    ValidationError,
    combomethod,
)

PBKDF2_ROUNDS = 2048
VALID_SEED_SIZES = [16, 20, 24, 28, 32]
VALID_WORD_LENGTHS = [12, 15, 18, 21, 24]
WORDLIST_DIR = Path(__file__).parent / "wordlist"


def normalize_string(txt):
    if isinstance(txt, bytes):
        utxt = txt.decode("utf8")
    elif isinstance(txt, str):
        utxt = txt
    else:
        raise ValidationError("String value expected")

    return unicodedata.normalize("NFKD", utxt)


class Mnemonic(object):
    def __init__(self, language):
        if language not in self.list_languages():
            raise ValidationError(
                f'Invalid language choice "{language}", must be one of {self.list_langauges()}'
            )
        self.language = language
        self.radix = 2048
        with open(WORDLIST_DIR / f"{language}.txt", "r", encoding="utf-8") as f:
            self.wordlist = [w.strip() for w in f.readlines()]
        if len(self.wordlist) != self.radix:
            raise ValidationError(
                f"Wordlist should contain {self.radix} words, "
                f"but it contains {len(self.wordlist)} words."
            )

    @combomethod
    def list_languages(_Mnemonic):
        return sorted(Path(f).stem for f in WORDLIST_DIR.rglob("*.txt"))

    @classmethod
    def detect_language(cls, raw_mnemonic):
        mnemonic = normalize_string(raw_mnemonic)
        words = mnemonic.split(" ")
        languages = cls.list_languages()

        language_counts = {lang: 0 for lang in languages}
        for lang in languages:
            wordlist = cls(lang).wordlist
            for word in words:
                if word in wordlist:
                    language_counts[lang] += 1

        # No language had all words match it, so the language can't be fully determined
        if max(language_counts.values()) < len(words):
            raise ValidationError("Language not detected")

        # Because certain wordlists share some similar words, we detect the language
        # by seeing which of the languages have the most hits, and returning that one
        most_matched_language = max(language_counts.items(), key=lambda c: c[1])[0]
        return most_matched_language

    def generate(self, num_words=12):
        if num_words not in VALID_WORD_LENGTHS:
            raise ValidationError(
                f"Invalid choice for number of words: {num_words}, should be one of "
                f"{VALID_WORD_LENGTHS}"
            )
        return self.to_mnemonic(os.urandom(4 * num_words // 3))  # 4/3 bytes per word

    def to_mnemonic(self, seed):
        if len(seed) not in VALID_SEED_SIZES:
            raise ValidationError(
                f"Invalid data length {len(seed)}, should be one of "
                f"{VALID_WORD_LENGTHS}"
            )
        checksum = hashlib.sha256(seed).hexdigest()
        bits = (
            bin(int(binascii.hexlify(seed), 16))[2:].zfill(len(seed) * 8) +
            bin(int(checksum, 16))[2:].zfill(256)[: len(seed) * 8 // 32]
        )
        result = []
        for i in range(len(bits) // 11):
            idx = int(bits[i * 11: (i + 1) * 11], 2)
            result.append(self.wordlist[idx])
        if self.language == "japanese":  # Japanese must be joined by ideographic space.
            result_phrase = u"\u3000".join(result)
        else:
            result_phrase = " ".join(result)
        return result_phrase

    def check(self, mnemonic):
        words = normalize_string(mnemonic).split(" ")
        # list of valid mnemonic lengths
        if len(words) not in VALID_WORD_LENGTHS:
            return False
        try:
            idx = map(lambda x: bin(self.wordlist.index(x))[2:].zfill(11), words)
            encoded_seed = "".join(idx)
        except ValidationError:
            return False
        l = len(encoded_seed)  # noqa: E741
        bits = encoded_seed[: l // 33 * 32]
        stored_checksum = encoded_seed[-l // 33:]
        raw_seed = binascii.unhexlify(hex(int(bits, 2))[2:].rstrip("L").zfill(l // 33 * 8))
        checksum = bin(int(hashlib.sha256(raw_seed).hexdigest(), 16))[2:].zfill(256)[: l // 33]
        return stored_checksum == checksum

    def expand_word(self, prefix):
        if prefix in self.wordlist:
            return prefix
        else:
            matches = [word for word in self.wordlist if word.startswith(prefix)]
            if len(matches) == 1:  # matched exactly one word in the wordlist
                return matches[0]
            else:
                # exact match not found.
                # this is not a validation routine, just return the input
                return prefix

    def expand(self, mnemonic):
        return " ".join(map(self.expand_word, mnemonic.split(" ")))

    @classmethod
    def to_seed(cls, checked_mnemonic: str, passphrase: str="") -> bytes:
        """
        :param str checked_mnemonic: Must be a correct, fully-expanded BIP39 seed phrase.
        :param str passphrase: Encryption passphrase used to secure the mnemonic.
        :returns bytes: 64 bytes of raw seed material from PRNG
        """
        mnemonic = normalize_string(checked_mnemonic)
        # NOTE: This domain separater ("mnemonic") is added per BIP39 spec to the passphrase
        # https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki#from-mnemonic-to-seed
        salt = "mnemonic" + normalize_string(passphrase)
        # From BIP39:
        #   To create a binary seed from the mnemonic, we use the PBKDF2 function with a
        # mnemonic sentence (in UTF-8 NFKD) used as the password and the string "mnemonic"
        # and passphrase (again in UTF-8 NFKD) used as the salt.
        stretched = hashlib.pbkdf2_hmac(
            "sha512",
            mnemonic.encode("utf-8"),
            salt.encode("utf-8"),
            PBKDF2_ROUNDS,
        )
        return stretched[:64]
