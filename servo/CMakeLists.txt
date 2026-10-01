cmake_minimum_required(VERSION 3.10)
project(pca9685_servo LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_executable(pca9685_servo src/pca9685_servo.cpp)

# On Debian/Ubuntu-family systems, linux/i2c-dev.h is provided by linux-libc-dev
# and no extra link libraries are needed for ioctl/read/write.
# If you need libi2c for other helpers, uncomment the next lines:
# find_library(LIBI2C i2c)
# if(LIBI2C)
#   target_link_libraries(pca9685_servo PRIVATE ${LIBI2C})
# endif()
