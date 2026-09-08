# MathNet quadratic anchor selection

## Reproducible source

- Dataset: [`ShadenA/MathNet`](https://huggingface.co/datasets/ShadenA/MathNet)
- Exact tag: `Algebra > Intermediate Algebra > Quadratic functions`
- Verified population: **719 problems**
- Selector: [`scripts/mathnet_anchor_search.py`](../scripts/mathnet_anchor_search.py)

Run from the repository root:

```bash
python3 -m pip install -r scripts/mathnet-requirements.txt
python3 scripts/mathnet_anchor_search.py
```

The generated indexes are placed under `.local-data/` and are not committed.
The automatic score is a first pass, not the pedagogical decision. It rewards
short wording, recognizable situations, lower symbolic density, and problems
that do not announce the quadratic method in their opening sentence.

## Recommended Module 1 anchors

| Rank | MathNet record | Student-facing idea | Why it belongs |
|---|---|---|---|
| 1 | [`02zc`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=16252) | **The Two-Stripe Flag:** choose the width of two crossing stripes so the striped and unstriped areas match. | Highly visual; experimentation is easy; subtracting the overlap creates the quadratic naturally. |
| 2 | [`022j`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=10856) | **The Furious Teacher's Grading Rule:** a mark is reduced by the same percentage as the original mark. Who ends with the highest result, and which students tie? | Funny and personally legible; exposes maximum, vertex, symmetry, and equal outputs. |
| 3 | [`029j`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=20419) | **The Triangle of Cans:** arrange 480 cans in growing rows and leave exactly 15 unused. How many complete rows are possible? | Physical and drawable; a growing pattern becomes a quadratic without formal-looking setup. |

The source language for records `02zc`, `022j`, and `029j` is Portuguese.
Any student-facing English version should be treated as a faithful translation
and retain the MathNet record link and original competition attribution.

## Strong alternatives

| MathNet record | Use case | Caution |
|---|---|---|
| [`0e4j`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=15445) | Accessible ratio story that produces a quadratic. | Update the dated boys/girls framing before student use. |
| [`0j2v`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=15219) | Coin-flip probability with a short quadratic resolution. | Requires probability knowledge. |
| [`059o`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=18771) | Enormous notebook iteration with a satisfying structural shortcut. | Better as an extension than a first entry point. |
| [`0e2j`](https://huggingface.co/datasets/ShadenA/MathNet/viewer/all/train?row=1822) | Soccer-ball height, landing point, and maximum. | Very accessible but more conventional than the top three. |

## Selection principles

1. A student should understand the situation before seeing an equation.
2. Drawing, building a table, guessing, or testing small cases should all be legitimate starts.
3. The quadratic should explain something surprising in the situation.
4. The three anchors should vary in representation: area, input/output symmetry, and growing pattern.
5. Contest difficulty is secondary to productive entry points for traditionally weaker students.
