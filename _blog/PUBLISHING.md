# Blog Publishing Workflow

> How to move a blog post from draft to live on [benchbox.dev/blog](https://benchbox.dev/blog/).

## Overview

Blog content is developed under `_blog/` in the single `BenchBox-dev/BenchBox` repository. When a post is ready, archive the final source under `_blog/{series}/published/` and place its ABlog copy under `docs/blog/` in the same change.

The release branch excludes `_blog/` but retains `docs/blog/` for versioned artifacts. A pull request to `develop` validates the post and the docs build. Merging to `develop` does not publish the post. benchbox.dev is built and deployed from `BenchBox-dev/benchbox-site`, which keeps copies of `docs/blog/` as `blog/` and of `_blog/` as `drafts/`. The post goes live when the change is replayed into that repository by pull request and merged to its `main` branch. The release flow publishes the Python package, not the site.

## Directory Structure

### Editorial source (`_blog/`)

Each blog series has a consistent structure:

```
_blog/
  STYLE_GUIDE.md          # Voice and content rules
  VOICE_REFERENCE.md      # Quick reference for tone
  {series-name}/
    outlines/             # Post outlines and planning
    research/             # Reference materials and notes
    drafts/               # Work-in-progress posts
    published/            # Finalized posts (series archive)
    archived/             # Deprecated versions
```

### Published site source (`docs/blog/`)

Published posts only, with ABlog frontmatter:

```
docs/blog/
  index.md                # Blog index (postlist + toctree)
  2025-01-22-welcome.md   # Published post
```

## Step-by-Step Publishing Process

### 1. Write the draft

Create the post in `_blog/{series}/drafts/`. Follow the style guide:

- **Voice**: Community-inclusive ("we" not "I"), enthusiastic but grounded
- **Claims**: Back with data, not opinions ("2.3x faster" not "much faster")
- **Platforms**: Neutral, no advocacy ("In this run, [platform A] finished [query] in [time] on [hardware]; [platform B] finished the same query in [time]. Include benchmark, scale, and phase so readers can interpret the measurement.")
- **Methodology**: Show your work, acknowledge limitations

See `_blog/STYLE_GUIDE.md` for the full guide and `_blog/VOICE_REFERENCE.md` for a quick tone reference. Sentence craft is defined in VOICE_REFERENCE. Do not copy it here.

### 2. Editorial check and content validation

Run the automated blog content validator on your draft:

```bash
uv run python scripts/blog_content_validation.py _blog/{series}/drafts/{post}.md
```

Or validate all blog content:

```bash
uv run python scripts/blog_content_validation.py
```

The validator checks:
- **Punctuation** (Error): Prohibits em-dashes (U+2014) and en-dashes (U+2013). Use ASCII hyphens or punctuation.
- **Platform advocacy** (Error): Prohibits platform-winner claims (`"destroys the competition"`, `"clearly superior"`).
- **Voice & tone** (Warning): Flags unsourced superlatives (`"revolutionary"`), vendor critiques (`"needs to fix"`), first-person pronouns (`"I think"`), and banned hedges (`"your mileage may vary"`, `"each platform has different strengths"`).
- **LLM writing tells** (Warning): Flags conversational residue (`"good point"`, `"you're right"`), temporal filler (`"going forward"`), empty throat-clearing (`"in today's digital landscape"`, `"whether you're a seasoned developer"`), AI vocabulary clichés (`"delve"`, `"rich tapestry"`), and formulaic essay conclusions (`"In conclusion"`).
- **Affirmation-denial couplets** (Advisory): Flags singleton denial echoes in post prose.

Exceptions can be annotated with `<!-- content-ok: <category> -->` or `<!-- content-ok -->`.

Before publication, also complete the `_blog/STYLE_GUIDE.md` editorial checklist by hand. Ensure that agent work on our writing (e.g. reviews, style checks, etc.) has not added LLM writing tells to our work.

### 3. Finalize the post

Once the editorial checklist is complete and the post is reviewed:

1. Move (or copy) the draft to `_blog/{series}/published/` for archival
2. Prepare the filename for publication: `YYYY-MM-DD-{slug}.md`

### 4. Add ABlog frontmatter

The post needs YAML frontmatter for ABlog to recognize it:

```yaml
---
blogpost: true
date: Mon DD, YYYY
author: Joe Harris
tags: tag1, tag2
---

# Post Title

Post content...
```

**Required fields:**

| Field | Format | Example |
|-------|--------|---------|
| `blogpost` | `true` | `true` |
| `date` | `Mon DD, YYYY` | `Jan 22, 2025` |
| `author` | Author name (must match `blog_authors` in `docs/conf.py`) | `Joe Harris` |
| `tags` | Comma-separated | `benchmarking, tpc-h, methodology` |

### 5. Place the post in the published site tree

Copy the finalized post to `docs/blog/` in the same repository:

```bash
cp _blog/{series}/published/{post}.md \
   docs/blog/YYYY-MM-DD-{slug}.md
```

Then adapt relative links for the destination. `_blog/{series}/published/` uses
`../images/{image}.png`, while `docs/blog/` uses `./images/{image}.png`.
Companion-post links in `docs/blog/` must include the companion's date-prefixed
filename. Do not rely on a blind copy when either form appears in the source.

### 6. Register the post in the blog index

Edit `docs/blog/index.md`. Add the post filename (without extension) to the `toctree`:

````md
```{toctree}
:maxdepth: 1
:hidden:

2025-01-22-welcome
YYYY-MM-DD-{slug}
```
````

The `postlist` directive at the top of the index will automatically pick up the new post for the recent posts listing.

### 7. Open the development pull request

Commit the archive copy, published copy, images, and index on a topic branch,
then open a pull request against `develop`. The Documentation workflow builds
Sphinx and ABlog, assembles the public site, runs link checks, and exercises the
public-site browser gate.

```bash
git add _blog/{series}/published/{post}.md \
  docs/blog/YYYY-MM-DD-{slug}.md docs/blog/images/{image}.png \
  docs/blog/index.md
git commit -m "docs(blog): publish {title}"
```

### 8. Replay the post into benchbox-site

After the development PR merges to `develop`, replay the change into `BenchBox-dev/benchbox-site` by pull request, following "Replaying core changes" in that repository's `AGENTS.md`. The replay copies `docs/blog/` to `blog/` and `_blog/` to `drafts/`, and `node scripts/replay-fidelity.mjs` must report 0 blocking. Squash-merging the replay pull request to `main` deploys the site. Do not use `make release-cut` / `make release-finalize` for site publishing; that flow releases the Python package.

The post will appear at `https://benchbox.dev/blog/YYYY-MM-DD-{slug}/` and will automatically be included in:

- Recent Posts sidebar widget
- Tag cloud (based on `tags` frontmatter)
- Archives (organized by date)
- RSS feed (`blog/atom.xml`)

## Quick Checklist

- [ ] Draft written in `_blog/{series}/drafts/`
- [ ] Style guide followed (`STYLE_GUIDE.md`)
- [ ] STYLE_GUIDE editorial checklist completed by hand
- [ ] ABlog frontmatter added (`blogpost`, `date`, `author`, `tags`)
- [ ] Filename follows convention: `YYYY-MM-DD-{slug}.md`
- [ ] `_blog/` archive keeps `../images/` links; `docs/blog/` copy uses `./images/`
- [ ] Companion links use exact date-prefixed filenames in `docs/blog/`
- [ ] Post copied to `docs/blog/` in the same repository
- [ ] Post added to `docs/blog/index.md` toctree
- [ ] Development PR checks pass against `develop`
- [ ] Change replayed into `BenchBox-dev/benchbox-site` and its pull request merged to `main`
- [ ] Deploy run succeeded; post checked live at `https://benchbox.dev/blog/`
