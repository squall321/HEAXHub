// HEAXHub cpp-cli 템플릿.
//
// 표준 호출 규약은 run.sh 가 다음 환경변수를 주입한다.
//   JOB_INPUT   - 입력 디렉터리
//   JOB_OUTPUT  - 출력 디렉터리
//   JOB_PARAMS  - params.json 경로
//
// 출력 규약:
//   <JOB_OUTPUT>/result.json 에 status / summary 를 기록한다.

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

namespace {

const char* env_or(const char* key, const char* fallback) {
    const char* v = std::getenv(key);
    return (v && *v) ? v : fallback;
}

void write_result(const std::string& output_dir) {
    const std::string path = output_dir + "/result.json";
    std::ofstream ofs(path);
    if (!ofs) {
        std::cerr << "[mytool] failed to open " << path << std::endl;
        return;
    }
    ofs << "{\n";
    ofs << "  \"status\": \"success\",\n";
    ofs << "  \"summary\": { \"message\": \"hello from HEAXHub cpp-cli template\" },\n";
    ofs << "  \"warnings\": [],\n";
    ofs << "  \"errors\": [],\n";
    ofs << "  \"outputs\": {}\n";
    ofs << "}\n";
}

}  // namespace

int main(int argc, char** argv) {
    const std::string input  = env_or("JOB_INPUT",  argc > 1 ? argv[1] : "");
    const std::string output = env_or("JOB_OUTPUT", argc > 2 ? argv[2] : "");
    const std::string params = env_or("JOB_PARAMS", argc > 3 ? argv[3] : "");

    std::cout << "[mytool] input=" << input
              << " output=" << output
              << " params=" << params << std::endl;

    if (output.empty()) {
        std::cerr << "[mytool] error: output dir is required (set JOB_OUTPUT)" << std::endl;
        return 2;
    }

    write_result(output);
    std::cout << "[mytool] done" << std::endl;
    return 0;
}
