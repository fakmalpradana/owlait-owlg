"""Terminal styling for the CLI, standard library only.

Colour is on when stdout is a TTY, unless NO_COLOR is set or OWLG_COLOR=never;
OWLG_COLOR=always forces it (for CI logs that render ANSI). Piped output stays
plain, so `owlg ... | grep` and the test-suite see exactly the words, no codes.
Every helper degrades to plain text when colour is off; nothing here changes
what is said, only how it looks.
"""
import os, sys

_CODES = dict(bold='1', dim='2', red='31', green='32', yellow='33', blue='34',
              magenta='35', cyan='36', white='37')


def enabled():
    mode = os.environ.get('OWLG_COLOR', '').lower()
    if mode == 'always': return True
    if mode == 'never' or os.environ.get('NO_COLOR'): return False
    return hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()


def c(text, *styles):
    """Wrap text in ANSI styles when colour is on."""
    if not styles or not enabled(): return str(text)
    return '\033[' + ';'.join(_CODES[s] for s in styles) + 'm' + str(text) + '\033[0m'


# semantic palette: one meaning, one colour, everywhere
def ok(t):    return c(t, 'green', 'bold')
def bad(t):   return c(t, 'red', 'bold')
def warn(t):  return c(t, 'yellow')
def dim(t):   return c(t, 'dim')
def key(t):   return c(t, 'cyan')
def num(t):   return c(t, 'bold')
def path(t):  return c(t, 'blue')
def tag(t):   return c(t, 'magenta', 'bold')


def hr(title=''):
    """A labelled rule: `── title ─────`."""
    width = 60
    if not title: return dim('─' * width)
    return dim('── ') + c(title, 'bold') + ' ' + dim('─' * max(1, width - len(title) - 4))


def kv(k, v, w=14):
    """Aligned `key : value` line."""
    return f"{key(f'{k:<{w}}')} {dim(':')} {v}"


def mb(nbytes):
    return f"{nbytes/1e6:.3f} MB"


def ratio(x):
    return num(f"{x:.2f}x")


def bound(delta):
    """The one word that matters most: how lossy is this file."""
    return ok('LOSSLESS') if int(delta) == 0 else warn(f"bound +/-{int(delta)} DN")


def table(rows, header=None, align=None):
    """Rows of strings -> aligned text table. align: 'l'/'r' per column."""
    rows = [list(map(str, r)) for r in rows]
    allr = ([list(map(str, header))] if header else []) + rows
    ncol = max(len(r) for r in allr)
    wid = [max(len(_plain(r[i])) if i < len(r) else 0 for r in allr) for i in range(ncol)]
    al = list(align or 'l' * ncol)
    def fmt(r, style=None):
        cells = []
        for i in range(ncol):
            t = r[i] if i < len(r) else ''
            pad = wid[i] - len(_plain(t))
            t = (' ' * pad + t) if al[i] == 'r' else (t + ' ' * pad)
            cells.append(c(t, style) if style else t)
        return '  '.join(cells).rstrip()
    out = []
    if header:
        out.append(fmt(header, 'bold'))
        out.append(dim('  '.join('─' * w for w in wid)))
    out += [fmt(r) for r in rows]
    return '\n'.join(out)


def _plain(t):
    """Length of the visible text, ignoring escape codes."""
    out, i = [], 0
    while i < len(t):
        if t[i] == '\033':
            j = t.find('m', i)
            i = (j + 1) if j > 0 else len(t)
        else:
            out.append(t[i]); i += 1
    return ''.join(out)


class Progress:
    """One updating line on a TTY (`label ▕████░░░▏ 3/8`); when piped, one plain
    line at most every 10% so logs stay short and readable."""
    def __init__(self, label, total):
        self.label, self.total, self.last = label, max(1, int(total)), -1
        self.tty = enabled() and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty()

    def update(self, i, note=''):
        if self.tty:
            frac = min(1.0, i / self.total); n = int(frac * 24)
            bar = c('█' * n, 'green') + dim('░' * (24 - n))
            end = '\n' if i >= self.total else '\r'
            sys.stdout.write(f"\r  {self.label} {bar} {i}/{self.total} {dim(note)}\033[K{end}")
            sys.stdout.flush()
        else:
            pct = i * 10 // self.total
            if pct != self.last or i >= self.total:
                self.last = pct
                print(f"    {self.label} {i}/{self.total} {note}".rstrip(), flush=True)

    def done(self, note=''):
        self.update(self.total, note)
