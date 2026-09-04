# LLM prompts

Each prompt file is a combination of:

* DEET prompt config (yaml) followed by a
* line with 50 dashes `--------------------------------------------------` followed by the
* full prompt

The config is probably in
the [latest experiments folder](https://github.com/destiny-evidence/impact-case-1/tree/taxonomy-classification/ic1/deet/projects/inout/data-extraction-experiments)
and the prompts are probably in
the [inout prompt folder](https://github.com/destiny-evidence/impact-case-1/blob/taxonomy-classification/ic1/deet/projects/inout/prompts).

You can use this little helper script to generate the prompt files:

```python
import re
import csv

REPLACE = re.compile(r'[<>:"/\\|?*\x00-\x1f -]')
DIVIDER = 50 * '!'
with open(".configs/prompts/config.yaml") as fp_yml:
    model_conf = fp_yml.read().strip()

with open(".configs/prompts/prompt_definitions.csv", newline="", encoding="utf-8") as fp_csv:
    reader = csv.DictReader(fp_csv)
    for row in reader:
        fname = REPLACE.sub('', row["attribute_label"])
        with open(f".configs/prompts/{fname}.txt", "w") as fp_conf:
            fp_conf.write(f'{model_conf}\n{DIVIDER}\n{row["prompt"]}')
```
