# Select AI agent teams: where to put the routing

`DBMS_CLOUD_AI_AGENT` gives you tools, tasks and teams, and the obvious design is to
register your capabilities as tools and let the model choose. We built that, measured
it on a scripted conversation, and it failed on **sequencing**, not on wording.

Measured on a five-turn script, with a non-reasoning model:

- on an opening question it called the identify tool **with no number at all**
- given the number on the next turn it **called nothing**, and asked for it again
- told the caller was verified, it **invented an amount and a due date** rather than
  calling the summary tool

The reasoning model fixed the invented figures and **none of the sequencing**, at
roughly twice the latency.


## The scripts

| | |
|---|---|
| [`00_inspect.sql`](00_inspect.sql) | **READ-ONLY.** Tools, tasks, teams, the `AGENT$` profiles, and the task-history view |
| [`01_one_tool.sql`](01_one_tool.sql) | One tool, with the every-turn instruction |
| [`02_current_prompt.sql`](02_current_prompt.sql) | Reading what the user really typed, with the count check |
| [`03_task_and_team.sql`](03_task_and_team.sql) | The task and team, with routing already moved out |

## The three findings

**The model paraphrases its own tool arguments.** Given a full sentence it called the
tool with the single word `Hi`. No tool description fixes that.

**You can read the real message out of the framework.** While a tool is running there
is a `RUNNING` row in `USER_AI_AGENT_TASK_HISTORY` whose `INPUT` is the user's message
word for word. Check that exactly one such row exists first: two chats running at the
same instant both have one and nothing in the view separates them.

**One tool, called every turn, beats four tools and a routing prompt.** PL/SQL decides
what happens next and returns the facts plus a suggested wording. The agent's only job
is putting it into English, which it does reliably, and the fast model is enough.

## The part that makes it defensible

The identity gate never depended on the model. The summary and detail code reads the
session row and refuses an unverified caller whatever the model decides. Had routing
been the only guard, the above would have been an incident rather than a measurement.

One workflow, one provider, two models, September 2026. A different task shape may
suit the routing loop better; nothing here says the framework is wrong.
