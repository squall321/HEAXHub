cmake_minimum_required(VERSION 3.20)
project(mytool CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(mytool src/main.cpp)
target_compile_options(mytool PRIVATE -Wall -Wextra -Wpedantic)

install(TARGETS mytool RUNTIME DESTINATION bin)
