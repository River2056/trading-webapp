# Issue tracker: GitHub

Issues and Wayfinder maps for this repository live in GitHub Issues at `River2056/trading-webapp`. Use the `gh` CLI from the repository root.

## General operations

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --json number,title,body,state,labels,assignees,url,comments`
- List: `gh issue list --state open --json number,title,labels,assignees,url`
- Comment: `gh issue comment <number> --body-file <file>`
- Edit: `gh issue edit <number> ...`
- Close: `gh issue close <number> --comment "..."`

Infer the repository from `git remote -v`; pass `--repo River2056/trading-webapp` when running outside the clone.

## Wayfinding operations

### Map and ticket labels

- A map is an issue labelled `wayfinder:map`.
- Every child decision ticket has exactly one type label: `wayfinder:research`, `wayfinder:prototype`, `wayfinder:grilling`, or `wayfinder:task`.

### Create a map

Create a normal issue with the Wayfinder map body, then label it:

`gh issue create --title "<map name>" --body-file <map.md> --label "wayfinder:map"`

### Create child tickets

Create each ticket as a native GitHub sub-issue of the map:

`gh issue create --title "<ticket name>" --body-file <ticket.md> --label "wayfinder:<type>" --parent <map-number>`

Create all tickets first. Add blocking relationships only in a second pass.

### Wire dependencies

A ticket blocked by another ticket uses GitHub's native issue dependency:

`gh issue edit <blocked-ticket> --add-blocked-by <blocking-ticket>`

Inspect relationships with:

`gh issue view <ticket> --json blockedBy,blocking`

### Claim a ticket

The assignee is the claim. Claim before doing any work:

`gh issue edit <ticket> --add-assignee "@me"`

An open, unassigned ticket is unclaimed.

### Find the frontier

1. List the map's native sub-issues through the GitHub REST API:
   `gh api repos/River2056/trading-webapp/issues/<map-number>/sub_issues --paginate`
2. Keep only open, unassigned children.
3. For each candidate, inspect `blockedBy`; it is on the frontier only when every blocker is closed.
4. Preserve the native sub-issue order when choosing the first frontier ticket.

### Resolve a ticket

1. Post the answer as a resolution comment.
2. Close the ticket.
3. Append only a one-line linked gist to the map's `Decisions so far` section.
4. Add new child tickets and dependencies in create-then-wire order.

### Read a map

Load only the map issue body for orientation. Query native sub-issues for open work; do not duplicate open tickets into the map body.

## When a skill says “publish to the issue tracker”

Create a GitHub issue.

## When a skill says “fetch the relevant ticket”

Run `gh issue view <number> --comments` and inspect its labels, assignees, parent, and dependencies as needed.
