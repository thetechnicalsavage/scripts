# Select AI NL2SQL in front of a real user

Three things break between a working demo and something a customer types into.

**1. The model cannot see your schema documentation.** `comments` defaults to `false`.
Sixty `comment on column` statements reached nobody until it was turned on.

**2. Conversation memory makes it confidently wrong.** With `conversation` on, a
follow-up pronoun resolved correctly, and two questions later a customer with seven
late payments was told they had never paid late. The generated SQL was right; the
narration came from a context about something else. A chat that is occasionally
confidently wrong about whether you paid your bill is worse than one that cannot
follow a pronoun.

**3. No prompt wording makes generated SQL safe.** The prompt is not what executes.

| | |
|---|---|
| [`01_profile.sql`](01_profile.sql) | The profile, every attribute, with the reason inline |
| [`02_vpd.sql`](02_vpd.sql) | Context, predicate function, policy. The null is the point |

## Two things to copy

**Compute the answer as a column.** The hardest question a billing customer asks is
why is this higher than last month. A `why_it_changed` column turns that into a
`SELECT` instead of a join the model has to invent.

**Returning `null` from the predicate function means unrestricted.** Without that
default you cannot add VPD to a database that already has tenants on it.

## What this is not

It is not access control against someone who can run arbitrary SQL: they simply do
not set the context. It is a guarantee about what the chat session can reach, which
is exactly the thing NL2SQL puts at risk. Sold as that it is true; sold as more it
is not.
