# Disclaimer & No-Warranty Notice

## This is example software, not financial advice.

The 24x7-trader score family is provided as an **educational example of
multi-agent orchestration patterns**. It is not a financial product, not
investment advice, not a recommendation to buy or sell any security, and
not an endorsement of any particular trading strategy, brokerage, or
data provider.

## NO WARRANTY

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Specific risks particular to this software

By running this score family, you acknowledge:

1. **You bear all risk of financial loss.** The score family can place
   real-money trades when configured to do so. Bugs in this software, in
   the third-party brokerage APIs it depends on, in the news/research APIs
   it queries, or in the AI models it invokes can cause incorrect orders
   to be placed, missed, mis-sized, mis-timed, or otherwise defective.
   No representation is made that the deterministic risk-envelope check,
   the multi-frame review, the adversarial pre-trade pass, or any other
   safety mechanism described in the documentation will catch every error.
   They will not.

2. **Past performance does not predict future results.** Any backtest,
   benchmark comparison, or anecdotal performance figure mentioned in
   the source video, the documentation, or the example scores is not a
   prediction of how this software will perform with your capital under
   future market conditions.

3. **AI model outputs are non-deterministic.** Running the same score
   on the same inputs at different times produces different reasoning
   and different proposals. The orchestration patterns reduce the
   variance but do not eliminate it. An AI model can hallucinate a
   thesis, mis-classify a position, miscalculate a stop, or produce a
   plausible-looking but wrong analysis.

4. **The reference scripts are minimal demonstrations.** They have not
   been audited for security, correctness, or production-readiness.
   They have known limitations documented in this repository's README
   and validation-gaps addendum. They may have unknown limitations.

5. **Configuration mistakes can cause harm.** Setting the wrong
   environment variable, pointing `BROKER_CMD` at a script that doesn't
   honor the two-key live-trading safety contract, or editing the
   risk envelope incorrectly can cause unintended live trades. Read
   every script before pointing real capital at it.

6. **Holiday handling, network outages, broker outages, market
   disruptions, regulatory changes, account restrictions, margin calls,
   pattern-day-trader rules, wash-sale considerations, tax
   implications, and other operational and legal concerns are entirely
   the operator's responsibility.** This software does not handle them
   and does not advise on them.

## Not a fiduciary; not a registered advisor

The authors and contributors of this software are not registered
investment advisors, broker-dealers, financial planners, or fiduciaries.
Nothing in the source, the documentation, or the AI-generated outputs
of this score family constitutes financial, investment, tax, or legal
advice. Consult a qualified professional before making investment
decisions.

## Use at your own risk

By configuring, running, modifying, or distributing this software, you
agree that you do so entirely at your own risk and that you indemnify
the authors and contributors against any losses, claims, or liabilities
arising from your use, the use by anyone you authorize, or the
unintended consequences of any of the above.

## Operator obligations

If you intend to run this software with real money, you should at
minimum:

- Run in paper mode for an extended period (months, not days) and
  understand the patterns of behavior the agent produces.
- Read every reference script in `_scripts/`. Understand what each
  CLI invocation does. Verify the API endpoints, the order types, and
  the credential paths.
- Configure the two-key live-trading safety mechanism (`BROKER_LIVE=1`
  + `LIVE_TRADING_ACKNOWLEDGED` file in workspace) consciously and
  not by accident.
- Monitor every run. Review every journal entry. Audit the trade log
  weekly.
- Use position sizes you can afford to lose entirely.
- Have an out-of-band kill switch (broker dashboard access, ability to
  manually liquidate) independent of this software.
- Maintain insurance, tax, and regulatory compliance entirely
  independently of this software.

## Reporting issues

If you discover a bug, especially one that could cause unintended
trades, please report it via the project's issue tracker. Do not
attempt to exploit issues or use undocumented behavior in production.

---

This disclaimer is in addition to (not in lieu of) the no-warranty and
limitation-of-liability provisions of the project's license (see
`LICENSE` and `LICENSE-AGPL` at the repository root). To the extent
of any conflict, the broader provisions apply.
