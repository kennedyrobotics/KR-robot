CXX ?= g++
CXXFLAGS ?= -O2 -std=c++17

all: pca9685_servo

pca9685_servo: src/pca9685_servo.cpp
	$(CXX) $(CXXFLAGS) $< -o $@

clean:
	rm -f pca9685_servo
