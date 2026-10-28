# AI Scan detail and accuracy

The scan pipeline preserves images up to 2048 pixels on the longest axis, normalizes EXIF rotation/mirroring, and uses JPEG quality 90 when encoding is needed. Scan URL downloads use the same resolution and quality. Already suitable JPEGs pass through without another lossy encode. General thumbnail defaults remain 768 pixels.

The mobile scan flow uses higher camera resolution and a separate upload compression policy so large photos are not reduced to 1024 pixels at quality 65 before reaching the backend. Both backend and mobile changes are needed for the complete capture-to-inference improvement. Higher resolution can increase upload size, inference cost, latency, and camera resource use; verify on target devices.

Meal vision responses now support up to 20 distinct food components. Repeated pieces of the same food belong in one row. The prompt asks the model to inspect the whole plate/bowl and small visible toppings, preserve identifiable components, and avoid inferring hidden ingredients from a dish's typical recipe. Ambiguous items should receive broader names and lower confidence. The mobile meal editor shows the existing localized review notice when a scanned ingredient has missing confidence or confidence below 0.8. This is a review heuristic, not a calibrated accuracy probability or a blocking confirmation gate.

## What is and is not verified

Unit checks verify orientation, resolution, repeated encoding, preservation of small components beyond eight rows, the 20-row bound, and evaluation accounting. Synthetic responses do not demonstrate improved recognition on real images. These changes do not establish 99.99% accuracy, identify hidden ingredients, or establish allergen absence.

The evaluator counts predictions on explicitly reviewed non-food images as false positives. Cases without identity annotations are not treated as negatives. A missed small topping counts against identity recall even when the main dish is correct. Food-detection accuracy alone cannot establish that every component was recognized.

## Real-photo evaluation

Use the existing paired provider evaluator described in [testing standards](testing-standards.md). Its reviewed manifest, explicit baseline/candidate prompt snapshots, and staging requirements still apply. No provider calls are required for CI.

The corpus should include:

- Plates and bowls with reviewed small components such as sesame seeds, chopped herbs, peanuts, and small vegetable pieces.
- Mixed Vietnamese dishes, soups, sauces, occluded ingredients, and visually similar foods. Annotate only what can actually be seen or independently verified; do not label a usual recipe as visual ground truth.
- Empty patterned dishes, utensils, reflections, packaging, blur, low light, and EXIF rotation/mirroring.
- Separate development and held-out photos; keep images from the same meal/capture session in one split.

Report identity precision and recall, negative-image false detections, parse/provider failures, portion errors, latency, and cost. Also evaluate the small-component subset separately. The existing `identity_precision` and `identity_recall` describe successfully parsed responses; always read them alongside parse/validation success rates, not as end-to-end success probabilities. The provider runner currently aborts on provider failures, so a failed run is not a passing accuracy evaluation.

Compare the complete old and new preprocessing pipelines separately from prompt-only comparisons: the evaluator uses the current service preprocessing for both prompt candidates. Save the baseline from the prior revision if measuring the total change. A 30-image smoke corpus is useful for detecting regressions but cannot substantiate a 99.99% production claim. Select a release target and a statistically adequate, representative sample before making numerical accuracy claims.

Manual mobile verification: photograph a small-topping bowl and select a large gallery image; inspect the uploaded resolution, complete the scan, verify that low-confidence results invite review, edit a misidentified component, and check portrait/mirrored images. Run Flutter analysis and the scan compression/upload tests on a workstation with the project's required Flutter/Dart SDK before release.
