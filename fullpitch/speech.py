"""Spoken commentary with Azure AI Speech (neural text to speech), keyless through Microsoft Entra ID.

Only text the server itself produced can be spoken: every card and answer the page receives carries a
speech id, and /api/speech/<id>.mp3 turns that id back into the text. Audio is cached on disk, so a line
is synthesised once however often it is played.
"""

import asyncio
import hashlib
import os
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path
from xml.sax.saxutils import escape

import re

VOICES = {"en": ("en-GB", "en-GB-RyanNeural"), "hu": ("hu-HU", "hu-HU-TamasNeural")}
MAX_TEXTS = 4000
MAX_SYNTH_PER_HOUR = int(os.environ.get("FULLPITCH_SPEECH_PER_HOUR", "300"))


def spoken(text, lang):
    """The line as a commentator says it: broadcast shorthand read aloud would sound wrong
    ("66'" is not 66 feet, "2-1" is not two minus one)."""
    if lang == "hu":
        text = re.sub(r"(\d+)\+(\d+)'(-?\w*)", r"\1 plusz \2. percben", text)  # "a 90+5'-ben"
        text = re.sub(r"(\d+)'(-?\w*)", r"\1. perc", text)                       # "35' óta" -> "35. perc óta"
        text = re.sub(r"\b(\d+)-(\d+)\b", r"\1 \2", text)                        # "2-1" -> "kettő egy"
    else:
        text = re.sub(r"(\d+)\+(\d+)'", r"\1 plus \2", text)
        text = re.sub(r"\b(?:at|in|since|from)\s+(\d+)'", r"in minute \1", text)
        text = re.sub(r"(\d+)'", r"minute \1", text)
        # scores the British way ("one nil"), but not "an 8-pass move"
        text = re.sub(r"\b(\d+)-(\d+)\b(?!\s*(?:pass|yard|metre|meter|man))",
                      lambda m: " ".join("nil" if g == "0" else g for g in m.groups()), text)
        text = re.sub(r"\bxG\b", "expected goals", text)
    return text


class Speech:
    def __init__(self, endpoint, cache_dir, credential=None):
        # endpoint: the AI Services resource, e.g. https://<name>.cognitiveservices.azure.com
        self.url = endpoint.rstrip("/") + "/tts/cognitiveservices/v1"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.texts: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
        self.synth_times: list[float] = []
        self.inflight: dict[str, asyncio.Task] = {}  # two viewers asking for the same new line share one synthesis
        self._token = (None, 0.0)
        if credential is None:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.credential = credential

    def register(self, text, lang):
        """A speech id for a line the server is about to send to a page."""
        text = " ".join(str(text).split())
        sid = hashlib.sha1(f"v2|{lang}|{text}".encode("utf-8")).hexdigest()[:16]  # v2: read with spoken()
        self.texts[sid] = (text, lang if lang in VOICES else "en")
        self.texts.move_to_end(sid)
        while len(self.texts) > MAX_TEXTS:
            self.texts.popitem(last=False)
        return sid

    def _bearer(self):
        token, expires = self._token
        if not token or time.time() > expires - 300:
            t = self.credential.get_token("https://cognitiveservices.azure.com/.default")
            self._token = (t.token, t.expires_on)
        return self._token[0]

    def _synthesise(self, text, lang):
        locale, voice = VOICES[lang]
        ssml = (f'<speak version="1.0" xml:lang="{locale}"><voice name="{voice}">'
                f'<prosody rate="+6%">{escape(spoken(text, lang))}</prosody></voice></speak>')
        req = urllib.request.Request(self.url, data=ssml.encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {self._bearer()}", "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "full-pitch"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    async def audio(self, sid):
        """mp3 bytes for a registered line; None if the id is unknown or the hourly budget is spent."""
        if sid not in self.texts:
            return None
        path = self.cache_dir / f"{sid}.mp3"
        if path.exists():
            return path.read_bytes()
        if sid not in self.inflight:
            now = time.time()
            self.synth_times = [t for t in self.synth_times if now - t < 3600]
            if len(self.synth_times) >= MAX_SYNTH_PER_HOUR:
                return None
            self.synth_times.append(now)
            self.inflight[sid] = asyncio.ensure_future(self._make(*self.texts[sid], path))
        try:
            return await asyncio.shield(self.inflight[sid])
        finally:
            if self.inflight.get(sid) is not None and self.inflight[sid].done():
                self.inflight.pop(sid, None)

    async def _make(self, text, lang, path):
        data = await asyncio.to_thread(self._synthesise, text, lang)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data
