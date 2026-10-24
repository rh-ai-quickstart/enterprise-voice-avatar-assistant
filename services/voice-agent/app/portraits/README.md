# Portraits

`default.png` is the face OpenSpline animates when `AVATAR_PROVIDER=openspline` and the chosen
face has no portrait of its own (the chart's faces have theirs in `chart/files/portraits`, mounted
at `AVATAR_PORTRAIT_DIR`); `OPENSPLINE_PORTRAIT` names another fallback file. It is `examples/girl.png` from
[SoulX-FlashHead](https://github.com/Soul-AILab/SoulX-FlashHead), the model OpenSpline runs,
used under the Apache License 2.0.

A replacement portrait works best as a front-facing head and shoulders shot, at least 512 x 512
pixels; OpenSpline fits it into a 512 x 512 frame.
