from collections import defaultdict, deque
import re
import time
from urllib.parse import urlsplit


class AutoModerator:
    def __init__(self):
        self.history = defaultdict(lambda: deque(maxlen=150))
        self.last_cleanup = 0

    def inspect(self, guild, user, content, mention_count, config, now=None):
        now = time.monotonic() if now is None else now
        if now - self.last_cleanup > 120:
            self.history = defaultdict(lambda: deque(maxlen=150), {k: v for k, v in self.history.items() if v and v[-1][0] > now - 120})
            self.last_cleanup = now
        history = self.history[(str(guild), str(user))]
        history.append((now, content.casefold().strip()))
        letters = [c for c in content if c.isalpha()]
        def blocked_link():
            for url in re.findall(r"https?://[^\s<>]+", content, re.I):
                try:
                    host = (urlsplit(url).hostname or "").lower().rstrip(".")
                except ValueError:
                    return True
                if not any(host == d.lower() or host.endswith("." + d.lower()) for d in config.allowed_domains if d):
                    return True
            return False
        checks = {
            "spam": lambda: sum(t > now - config.spam.window for t, _ in history) > config.spam.threshold,
            "flood": lambda: sum(t > now - config.flood.window and text == content.casefold().strip() for t, text in history) > config.flood.threshold,
            "caps": lambda: len(letters) >= 10 and sum(c.isupper() for c in letters) / len(letters) * 100 >= config.caps.threshold,
            "mentions": lambda: mention_count > config.mentions.threshold,
            "links": blocked_link,
            "invites": lambda: bool(re.search(r"(?:discord\.gg/|discord(?:app)?\.com/invite/)", content, re.I)),
        }
        for name, predicate in checks.items():
            rule = getattr(config, name)
            if rule.enabled and predicate():
                return name, rule
        return None
