# OpenSpline portraits

The faces offered in the UI when `voiceAgent.avatarProvider` is `openspline`, one JPEG per entry
of `openspline.faces`, named `<id>.jpg`. The chart puts them in the ConfigMap `avatar-portraits`
(at most about 700 KB of pictures in all) and mounts it in the voice agent, which hands the
chosen portrait to OpenSpline, and in the RAG API, which serves the picker's thumbnails.

`meera.jpg`, `lisa.jpg`, `professor.jpg` and `acharya.jpg` are single frames of the example GIFs
in OpenSpline's repository (`media/examples/*.gif` at commit 6d53966, MIT License, Copyright (c)
2026 openspline contributors), cropped to head and shoulders and enlarged to 512 pixels (from
240, or from 180 for Professor, frame 30, and Acharya, frame 36, whose GIFs are shorter). The
other examples are public figures and are left out. The GIFs are small, so the video is softer
than it would be from the original portraits;
a sharper picture (front-facing, at least 512 x 512, mouth closed) under the same name replaces
one. To add a face, put `<id>.jpg` here and add `{id, name, gender}` to `openspline.faces`.
