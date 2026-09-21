# scripts

Scripts referenced from [thetechnicalsavage.com](https://thetechnicalsavage.com).

Everything here is original work, written to be read before it is run. Licensed MIT,
so you can take it, change it, and ship it.

## What is here

| | |
|---|---|
| [`wlst/`](wlst/) | WebLogic Scripting Tool, online and offline |
| [`shell/`](shell/) | Shell helpers for the same jobs |
| [`oracle-ai/`](oracle-ai/) | Select AI, DBMS_CLOUD, AI Vector Search and OML on 26ai |

## Before you run anything

**Read the script.** Every one starts with a header saying what it touches and whether
it changes anything. Scripts that only read are marked `READ-ONLY`. Scripts that change
state say so, and none of them change state without an explicit flag.

**No credentials are hardcoded.** Every script reads them from the environment or from
a WLST user-config file. If you find a password in here, it is a bug, please open an
issue.

**Test somewhere you can afford to break.** These are written from real use, but your
domain is not my domain.

## Linking

Posts link to a specific commit rather than to `main`, so a link in a two-year-old
article still shows the code that article was describing.

## Contributing

Corrections are welcome, especially "this flag does not exist on version X". Open an
issue with the version you are on.
