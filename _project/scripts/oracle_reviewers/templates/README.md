# Review templates: slot contract (v1)

Three prompt templates (`standard.md`, `deep.md`, `gate.md`) rendered by filling
typed slots with change data. Templates are versioned review machinery: changes
to this directory must themselves be reviewed under the `gate` template.

## Selection

Jev chooses the template during classification (`standard` | `deep` | `gate`).
Constraint: a change with gate signals set may receive `gate` or `deep`, never
`standard`. Below the confidence threshold the renderer falls back to
`standard`, or `gate` when gate signals are set.

## Data fences

`file_priority` and `change_data` are untrusted: diffs, filenames, and scored
rows are author-influenced. Each template marks both sections with a
`%%FENCE%%` token; the renderer replaces every occurrence with a fresh 128-bit
nonce per render, so colliding text inside the data (including a literal
`%%FENCE%%` string) can never form a parseable section. The instruction prose
treats fence-claiming text anywhere as data. Templates must fence exactly two
sections; the renderer refuses anything else. `gate_flags` is a count written
by code, never a path, so author-chosen file names stay inside the fences.

## Slot validation

The renderer rejects unknown or missing slots, over-byte values, a
`template_name` that does not match the rendered file, a `size_bucket` or
`capability` outside the enums, non-integer `pr` / `max_defects` /
`template_version`, non-sha `base` / `head`, a `repo` outside `owner/name`
shape, non-numeric `confidence`, and a non-JSON `schema`. A compromised or
buggy caller cannot smuggle prose through a trusted slot.

## Slots

Every slot is filled by the renderer on every dispatch. No slot may be left
empty; an empty value renders as `none`. Slot text is data, except the fixed
instruction prose of the template itself.

| Slot | Type | Max bytes | Contents |
|---|---|---|---|
| `template_name` | identifier | 16 | `standard`, `deep`, or `gate`; must match the file |
| `template_version` | integer | 4 | template version, currently `1` |
| `repo` | `owner/name` | 128 | repository under review |
| `pr` | integer | 8 | pull request number |
| `base` | hex sha | 40 | base commit |
| `head` | hex sha | 40 | reviewed head commit |
| `size_bucket` | identifier | 8 | Jev size call: `trivial` … `max` |
| `capability` | identifier | 8 | Jev capability call: `routine` or `deep` |
| `confidence` | decimal | 6 | top-bucket probability, e.g. `0.89` |
| `gate_flags` | text | 2048 | count of changed review-machinery files, or `none` |
| `file_priority` | table | 8192 | `path \| score \| impact` rows, score descending, cut to fit, or `none`; fenced |
| `max_defects` | integer | 4 | protocol defect cap from policy |
| `prior_rule` | prose | 2048 | first / ship / follow-up prior-defect rule |
| `change_data` | prose+diff | 100000 | file list, then inline diff or read instructions per brief mode; fenced |
| `schema` | JSON | 16384 | verdict JSON Schema |

Rendered briefs must fit `brief_max_bytes` (policy, currently 100000).
`change_data` dominates the budget; when the inline diff does not fit, the
renderer falls back to file-list mode and the reviewer reads from the working
directory. Placeholders use Python `str.format` braces; literal braces in
template prose must be doubled. Brace sequences inside slot values are inert:
formatting applies to the template, never to the values.
