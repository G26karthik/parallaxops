# Experiment: does masking moving people help SfM? (Gate 5, written before the runs)

This plan was written and committed before any masked run existed. Results are added to this file
afterwards in a separate section; the plan section is not edited.

## Question

Structure-from-motion assumes a static scene. When people walk through the view, features on them
move relative to the scene and can corrupt matching or bundle adjustment. Does removing those
pixels from feature extraction improve the reconstruction, and what does it cost?

"Masking" here means **2D panoptic segmentation of the photos** to keep SfM from extracting
features on people. It is not 3D segmentation and nothing in this experiment claims 3D labelling.

## Data

TUM RGB-D `freiburg3_walking_xyz` (Sturm et al., IROS 2012): a handheld Kinect RGB camera moves
along the x, y and z axes in an office while two people walk through the scene. The ground-truth
camera trajectory comes from a motion-capture system. Only the RGB images and `groundtruth.txt`
are used; depth is ignored.

- Source: `https://cvg.cit.tum.de/rgbd/dataset/freiburg3/rgbd_dataset_freiburg3_walking_xyz.tgz`
  (0.54 GB), fetched by hand into `$PARALLAXOPS_HOME/data/tum` and never committed.
- **License: not confirmed.** The download page says the data is shared with other researchers and
  asks for citation; it names no license. The data is therefore not redistributed here and the
  screenshots and tables in this repository contain only numbers.
- Frames: every 10th RGB frame (30 Hz source, so about 3 Hz), 640x480. Each frame gets a ground-truth
  pose from the nearest `groundtruth.txt` entry within 0.02 s; frames without one are used for
  reconstruction but not scored.
- Ground-truth poses are given as the camera pose in the motion-capture world frame. Camera centres
  are compared after a RANSAC similarity fit (the same evaluator as Gate 1 and Gate 4). Rotation
  error is not reported for this data: the mapping between the mocap marker frame and the optical
  frame is not verified here.

## Arms

Same images, same configuration, same seeds (0, 1, 2), interleaved per repetition:

| Arm | Feature extraction |
|-----|--------------------|
| `unmasked` | all pixels |
| `masked` | no features on pixels covered by a person, grown by 6 px |

Mask model: DETR ResNet-50 panoptic (`facebook/detr-resnet-50-panoptic`), Apache-2.0 on its model
card, trained on COCO 2017 panoptic. Classes: `person`. Confidence threshold 0.85. It runs in a
separate environment (CPU PyTorch) as a subprocess; the platform's worker environment does not
depend on it. Mask generation time is measured and reported separately from SfM time.

Everything else (matching policy, mapper, camera model, image size) is the default configuration.

## Metrics

Per run: registered fraction, 3D point count, mean reprojection error, camera-centre error against
ground truth (absolute and divided by the spread of the ground-truth camera path), SfM wall clock.
Per arm: mean and the min-max range over the three seeds. Masking cost: segmentation seconds,
fraction of pixels ignored.

Reprojection error and point count are proxies and are not interchangeable with ground-truth
camera error. More points is not better by itself.

## Hypotheses and decision rule

- **H1:** the masked arm has lower camera-centre error than the unmasked arm.
- **H2:** the masked arm registers at least as large a fraction of the images.
- **H3:** the masking cost (segmentation time) is larger than any SfM time it saves.

A difference counts as an effect only if the arm means differ by more than the larger of the two
arms' min-max ranges over the three seeds. Anything smaller is reported as "no measurable
difference". With three seeds this is a coarse rule, deliberately conservative.

## Known limits (stated now, not after the fact)

- One sequence, one camera, one segmentation model. A result here does not generalise to other
  scenes or to other kinds of motion (cars, foliage, water).
- A feasibility run of the unmasked arm alone was done first to confirm that SfM can reconstruct this
  data at all. No masked run existed at that point.
- Detection misses (partially visible people, shadows, held objects) leave some moving pixels in
  the masked arm; the experiment measures the pipeline as built, not a perfect mask.
- Kinect RGB frames are low resolution and rolling-shutter distorted; absolute errors will be
  larger than on the ETH3D DSLR data and are not comparable with it.
- Masking can also remove useful static features on people-adjacent surfaces. The dilation of 6 px
  is a fixed choice, not tuned.
