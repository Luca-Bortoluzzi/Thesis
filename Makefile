LAB ?= dos_lab
MODE ?= full

.PHONY: simulate baseline attack mitigation stop

simulate:
	./simulate.sh $(LAB) $(MODE)

baseline:
	./simulate.sh $(LAB) baseline

attack:
	./simulate.sh $(LAB) attack

mitigation:
	./simulate.sh mitigation full

stop:
	./stop.sh labs/$(LAB)
