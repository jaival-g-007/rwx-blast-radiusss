IMAGE ?= rwx-blast-radius
KIT ?= ./kit
KIT_DIR := $(abspath $(KIT))
PLATFORM := linux/amd64

.PHONY: build sample test

build:
	docker build --platform $(PLATFORM) --tag $(IMAGE) .

sample: build
	docker run --rm --platform $(PLATFORM) --network none --cpus 2 \
		--memory 4096m --memory-swap 4096m --pids-limit 256 --read-only \
		--tmpfs /tmp:rw,size=64m,mode=1777 --cap-drop ALL \
		--security-opt no-new-privileges --user 65534:65534 --env HOME=/tmp \
		--volume "$(KIT_DIR)/sample/commits:/data/commits:ro" \
		--volume "$(KIT_DIR)/sample/tests.json:/data/tests.json:ro" \
		$(IMAGE) > output.jsonl
	python3 "$(KIT_DIR)/score.py" "$(KIT_DIR)/sample" "$(KIT_DIR)/sample/answers.json" output.jsonl

test:
	python3 -m unittest discover -s tests -v
