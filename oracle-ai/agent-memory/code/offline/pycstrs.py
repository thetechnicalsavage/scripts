# v1.0 2026-10-09  Dump string constants with their source line numbers from installed .pyc files (offline).
import marshal, dis, sys, os, types
def walk(code):
    yield code
    for c in code.co_consts:
        if isinstance(c, types.CodeType): yield from walk(c)
def dump(pyc):
    with open(pyc, 'rb') as f: f.read(16); code = marshal.load(f)
    for co in walk(code):
        for ins in dis.get_instructions(co):
            if ins.opname in ('LOAD_CONST', 'RETURN_CONST') and isinstance(ins.argval, str) and len(ins.argval) > 12:
                ln = ins.positions.lineno if ins.positions else None
                yield ln, co.co_name, ins.argval
site = sys.argv[1]
for root, _, files in os.walk(site):
    for fn in files:
        if fn.endswith('.pyc'):
            p = os.path.join(root, fn); rel = os.path.relpath(p, os.path.dirname(site))
            for ln, name, s in dump(p):
                print(f"{rel}:{ln}\t{name}\t{s!r}")
