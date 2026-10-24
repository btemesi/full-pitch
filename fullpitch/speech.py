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

VOICES = {"en": ("en-GB", "en-GB-RyanNeural"), "hu": ("hu-HU", "hu-HU-TamasNeural")}
MAX_TEXTS = 4000
MAX_SYNTH_PER_HOUR = int(os.environ.get("FULLPITCH_SPEECH_PER_HOUR", "300"))


class Speech:
    def __init__(self, endpoint, cache_dir, credential=None):
        # endpoint: the AI Services resource, e.g. https://<name>.cognitiveservices.azure.com
        self.url = endpoint.rstrip("/") + "/tts/cognitiveservices/v1"
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.texts: "OrderedDict[str, tuple[str, str]]" = OrderedDict()
        self.synth_times: list[float] = []
        self._token = (None, 0.0)
        if credential is None:
            from azure.identity import DefaultAzureCredential
            credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        self.credential = credential

    def register(self, text, lang):
        """A speech id for a line the server is about to send to a page."""
        text = " ".join(str(text).split())
        sid = hashlib.sha1(f"{lang}|{text}".encode("utf-8")).hexdigest()[:16]
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
                f'<prosody rate="+6%">{escape(text)}</prosody></voice></speak>')
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
        now = time.time()
        self.synth_times = [t for t in self.synth_times if now - t < 3600]
        if len(self.synth_times) >= MAX_SYNTH_PER_HOUR:
            return None
        self.synth_times.append(now)
        text, lang = self.texts[sid]
        data = await asyncio.to_thread(self._synthesise, text, lang)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return data
