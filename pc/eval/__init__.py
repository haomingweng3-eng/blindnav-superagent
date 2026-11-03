"""Offline evaluation harness (skeleton).

Rule of the harness: the *system under test* only ever sees RGB + orientation/step proxies
(``SanpoSession.inputs``); ground-truth depth / segmentation (``SanpoSession.gt``) is used for scoring only.
"""
