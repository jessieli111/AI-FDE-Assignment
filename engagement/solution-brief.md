# Harbourline Support Intake: Solution Brief

*Page 1 of the client brief, week 1 draft. All emails in this pilot are synthetic.*

## The problem

Harbourline's support inbox mixes coverage questions, billing problems, claim
status checks and messages that need no action at all. Before an agent can help,
they read each email, work out what is being asked, and notice what's missing.
Often that's the policy number or claim ID, so the first reply only asks for it.
That reading and sorting is repeated on every email, and the agent does it
before any real work starts.

## The user

A **Harbourline support agent** who works the shared inbox. For each email they
need three answers at a glance:

1. **What is this about?** Coverage, billing, claim, or other.
2. **Can I act on it yet, and if not, what do I need to ask for?**
3. **A first reply I can check, edit and send myself.**

The agent stays in charge. The intake suggests; the agent decides and sends.

## What the pilot does

One email in, one validated record out: `category`, `missing_fields`,
`next_action` (`ask`, `draft` or `escalate`), a suggested `customer_message`,
and a `confidence` score. Each record is checked against the schema in code. A
record that fails is sent back to the model with the error, at most 3 calls in
total. Every call is logged with its outcome, tokens and latency.

## What this pilot will not do

1. **Send email.** Every customer message is a draft for an agent to review
   and send.
2. **Pay, approve or deny a claim.** It doesn't decide anything about money or
   coverage, and its drafts never promise either.
3. **Look up policies, claims or billing records.** It has no access to
   Harbourline systems (no retrieval), so it never states what a policy covers
   or where a claim stands.
4. **Create or update tickets** in Harbourline's support tools.
5. **Handle real customer data** during the pilot. Only synthetic emails are
   used until data handling is agreed.

## Open questions for Harbourline

- Which fields does an agent need for each category? The pilot assumes a
  policy number for coverage and billing, and a claim ID for claims.
- When should an email go to a senior agent (`escalate`)?
- Which languages must replies support? The pilot replies in the customer's
  first language.
