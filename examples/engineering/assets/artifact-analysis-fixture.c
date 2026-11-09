/* Fixture for examples/engineering/artifact-analysis.yaml (GH #499).
 *
 * Build it, then point the score at the binary:
 *   gcc -O1 -o "$MARIANNE_SCRATCH/greet" artifact-analysis-fixture.c   # or any writable path
 *   mzt run examples/engineering/artifact-analysis.yaml --var artifact_path=/abs/path/greet
 *
 * Facts a characterization can state and a reviewer can check against REA's
 * evidence bundle: one defined exported symbol beyond the C runtime (`shout`),
 * one banner string, a dynamically linked x86-64 ELF.
 */
#include <stdio.h>
#include <string.h>

static const char *BANNER = "artifact-analysis fixture v1";

int shout(const char *s) { return (int)strlen(s) * 2; }

int main(int argc, char **argv) {
  const char *who = argc > 1 ? argv[1] : "world";
  printf("%s: hello, %s (%d)\n", BANNER, who, shout(who));
  return 0;
}
