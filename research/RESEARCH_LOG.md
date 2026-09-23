# Research log

## 2026-09-23 — Session 1 (analysis + research, no production code yet)

### What was examined
- Unzipped and read `Queue Management`, `Shelf monitoring`, `Shopper analytics` archives (all `.py`, configs, logs, both Kaggle notebooks incl. outputs).
- Inspected both TFLite models with LiteRT: `1.tflite` = EfficientDet-Lite0 COCO (320×320 uint8, 25 detections), identical MD5 in both folders; `shelf_void_grid_int8.tflite` = 320×320 → 10×10×5 grid.
- Extracted the shelf training curve from notebook output (train 1.37→0.09, val best 1.19 @ epoch 10, 2.77 @ epoch 18).
- Read `queue_events.jsonl`: false `PREDICTED_CONGESTION` alert with `queue_count 1, predicted_30s 78.4`.
- Read `sihfinal.pptx` (text + rendered slides + embedded images): Results slide dashboard is labelled "DEMO"; HX711 shown as live hardware.
- Read `StoreMind_Advanced_BluePill_FreeRTOS_Guide.pdf`.
- Cloned and read https://github.com/adarsh2133/SIH-Qualcomm-RetailAI-XLink (all `.py`, README, notebook, runtime JSONs).
- Verified PPT references on the web: 4 of 5 exist; queue paper venue is IJSREM; "BareMetal on the Edge" lecture not found.

### Checked by running code
- Erlang-C counter recommendation + cross-correlation lag estimator (`03_NOVELTY` snippet) — ran on synthetic data, lag of 12 min recovered exactly.
- `tools/camera_check.py` — ran on a synthetic video file (29 FPS measured, snapshot saved). Not yet tested on a Pi camera / RTSP camera — **team to test on the Pi**.

### Not done yet (next sessions)
- Rewriting the pipeline code (planned structure in `04_ARCHITECTURE.md`).
- STM32 firmware review (firmware not in the folder — please add it).
- Real accuracy numbers (need the team's recorded videos).

### Sources used
Hardware
- Raspberry Pi AI HAT docs: https://www.raspberrypi.com/documentation/accessories/ai-hat-plus.html
- AI HAT+ launch ($70 / $110): https://www.raspberrypi.com/news/raspberry-pi-ai-hat/
- AI HAT+ 2 ($130, 40 TOPS, 8 GB): https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/
- Pi 5 price rises 2026: https://www.raspberrypi.com/news/more-memory-driven-price-rises/
- Pi 5 lacks H.264 hardware codec: https://news.ycombinator.com/item?id=38068801
- Ultralytics Raspberry Pi guide (YOLO26n NCNN ≈ 67 ms on Pi 5): https://docs.ultralytics.com/guides/raspberry-pi/
- Hailo-8L on Pi 5 benchmark (YOLOv8s ≈ 128 FPS, batch 8): https://community.hailo.ai/t/raspberry-pi-5-with-hailo-8l-benchmark/746
- Jetson Nano YOLOv8n TensorRT benchmark (38.6 / 12.3 FPS): https://github.com/PragalvaXFREZ/jetson-nano-yolo-bench
- JetPack 4 end of life: https://forums.developer.nvidia.com/t/announcing-end-of-life-for-nvidia-jetpack-4-with-the-release-of-jetpack-4-6-6/314302
- Jetson Orin Nano Super India price: https://www.indiamart.com/proddetail/nvidia-jetson-orin-nano-super-developer-kit-2855511390333.html
- Radxa Dragon Q6A (QCS6490): https://www.cnx-software.com/2025/10/27/radxa-dragon-q6a-a-qualcomm-qcs6490-edge-ai-sbc-with-gbe-wifi-6-three-camera-connectors/
- RUBIK Pi 3: https://www.hackster.io/news/thundercomm-s-rubik-pi-3-edge-ai-board-developed-with-qualcomm-goes-on-sale-for-179-529c9a8384c2 · https://www.thundercomm.com/rubik-pi-3-99/
- RB3 Gen 2 (Edge Impulse docs): https://docs.edgeimpulse.com/hardware/boards/qualcomm-rb3-gen-2-dev-kit · India listing: https://www.indiamart.com/proddetail/thundercomm-qualcomm-rb3-gen-2-development-kit-2854743101897.html
- Arduino UNO Q: https://docs.arduino.cc/hardware/uno-q · review: https://www.tomshardware.com/raspberry-pi/arduino-uno-q-review · India price: https://robocraze.com/products/official-arduino-uno-q-sbc

Qualcomm AI
- Qualcomm AI Hub getting started: https://workbench.aihub.qualcomm.com/docs/hub/getting_started.html
- Person-Foot-Detection model: https://aihub.qualcomm.com/iot/models/foot_track_net · https://huggingface.co/qualcomm/Person-Foot-Detection
- YOLOv8-Detection on AI Hub (AGPL): https://aihub.qualcomm.com/iot/models/yolov8_det

Data & models
- SKU-110K: https://docs.ultralytics.com/datasets/detect/sku-110k · https://github.com/eg4000/SKU110K_CVPR19
- Grocer-Help (Indian stores): https://www.nature.com/articles/s41598-026-42266-9
- Roboflow retail/empty-shelf sets: https://universe.roboflow.com/browse/retail
- YOLOE (open-vocabulary, auto-labelling): https://docs.ultralytics.com/models/yoloe

Privacy / law
- DPDP Rules 2025: https://en.wikipedia.org/wiki/Digital_Personal_Data_Protection_Rules,_2025 · timeline: https://www.consently.in/blog/dpdp-rules-2025-implementation-timeline-india

Queueing
- Erlang C overview: https://www.techtarget.com/searchunifiedcommunications/definition/Erlang-C · Erlang C vs A evaluation: https://myweb.ecu.edu/robbinst/PDFs/Comparing%20Erlang%20A%20and%20Erlang%20C%20-%20WP.pdf

PPT references verified
- Smart Shelf Monitoring Using YOLO: https://ieeexplore.ieee.org/abstract/document/10940703
- CV Based Retailer Shelves Monitoring System: https://ieeexplore.ieee.org/document/11277851/
- Real-Time Queue Detection… (IJSREM): https://ijsrem.com/download/real-time-queue-detection-and-management-system-using-yolo-object-detection/

Prices are approximate and change often — recheck before buying.
