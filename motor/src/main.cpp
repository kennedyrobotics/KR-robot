#include "MotorTB6612.hpp"
#include <iostream>
#include <thread>

int main(int argc, char** argv) {
    int gpio_dirA = 25;
    int gpio_dirB = 24;
    std::string pwmchip_path = "/sys/class/pwm/pwmchip0";
    int pwm_index = 0;
    uint32_t period_ns = 50000;

    if (argc > 1) gpio_dirA = std::stoi(argv[1]);
    if (argc > 2) gpio_dirB = std::stoi(argv[2]);
    if (argc > 3) pwmchip_path = argv[3];
    if (argc > 4) pwm_index = std::stoi(argv[4]);
    if (argc > 5) period_ns = static_cast<uint32_t>(std::stoul(argv[5]));

    try {
        MotorTB6612 motor(gpio_dirA, gpio_dirB, pwmchip_path, pwm_index, period_ns);

        std::cout << "Ramping forward..." << std::endl;
        for (int i = 0; i <= 100; ++i) {
            motor.setSpeed(i / 100.0f);
            std::this_thread::sleep_for(std::chrono::milliseconds(15));
        }

        std::this_thread::sleep_for(std::chrono::milliseconds(500));

        std::cout << "Ramping reverse..." << std::endl;
        for (int i = 0; i <= 100; ++i) {
            motor.setSpeed(-i / 100.0f);
            std::this_thread::sleep_for(std::chrono::milliseconds(15));
        }

        std::cout << "Brake then coast." << std::endl;
        motor.brake();
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        motor.coast();
        std::cout << "Done." << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "ERROR: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
