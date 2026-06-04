// HEAXHub demo · C++ executable
//
// Prints a one-line summary to stdout and writes output/result.json with
// {"ran": "cpp", "count": <arg>}. The count is taken from argv[1] when
// provided, otherwise defaults to 10.

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

int main(int argc, char** argv) {
    int count = 10;
    if (argc > 1) {
        try {
            count = std::stoi(argv[1]);
        } catch (...) {
            std::cerr << "warning: ignoring non-integer count '" << argv[1]
                      << "', using default 10\n";
        }
    }

    std::cout << "HEAXHub C++ demo: count=" << count << "\n";

    namespace fs = std::filesystem;
    std::error_code ec;
    fs::create_directories("output", ec);

    std::ofstream f("output/result.json");
    if (!f) {
        std::cerr << "error: cannot open output/result.json for writing\n";
        return 1;
    }
    f << "{\"ran\": \"cpp\", \"count\": " << count << "}\n";
    return 0;
}
