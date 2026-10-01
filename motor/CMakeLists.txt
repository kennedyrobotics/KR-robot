cmake_minimum_required(VERSION 3.10)
project(tb6612_motor CXX)
set(CMAKE_CXX_STANDARD 17)
add_executable(tb6612_motor
    src/main.cpp
    src/MotorTB6612.hpp
)
