# LeadPoet - Bittensor Subnet 71

LeadPoet is a decentralized AI sales agent subnet for high-quality B2B lead generation on Bittensor.

## Project Structure

```
neurons/           # Main entry points for miners and validators
  miner.py         # Miner node - sources and submits leads
  validator.py     # Validator node - validates leads via consensus
  auditor_validator.py  # LLM-based lead auditor

gateway/           # FastAPI gateway service (runs in TEE)
  api/             # REST API endpoints (submit, validate, reveal, etc.)
  tasks/           # Background tasks (epoch lifecycle, consensus, etc.)
  tee/             # TEE/enclave signing and attestation
  utils/           # Helpers (rate limiting, merkle, storage, etc.)

validator_models/  # Validation logic
  automated_checks.py  # Multi-stage lead validation pipeline
  industry_taxonomy.py # Industry/sub-industry classifications
  reputation_score.py  # Company reputation scoring (0-48 points)

miner_models/      # Lead sourcing and classification
  lead_sorcerer_main/  # Dynamic lead generation pipeline
  lead_auditor.py      # LLM lead quality auditor
  intent_model.py      # Buyer intent classification

Leadpoet/          # Core library
  base/            # Base miner/validator/neuron classes
  protocol.py      # Synapse protocol definitions
  validator/       # Consensus and reward logic
  utils/           # Logging, cloud DB, compliance

leadpoet_audit/    # Public audit CLI tool
leadpoet_canonical/ # Canonical chain verification
```

## Key Commands

```bash
# Run miner
python neurons/miner.py --wallet_name miner --wallet_hotkey default --netuid 71 --subtensor_network finney

# Run validator
python neurons/validator.py --wallet_name validator --wallet_hotkey default --netuid 71 --subtensor_network finney

# Run gateway (development)
cd gateway && uvicorn main:app --reload

# Run audit tool
leadpoet-audit report <epoch_number>
leadpoet-audit logs --hours 4
```

## Environment Variables

Required for validators (see `env.example`):
- `TRUELIST_API_KEY` - Email validation
- `SCRAPINGDOG_API_KEY` - LinkedIn verification via Google Search
- `OPENROUTER_KEY` - LLM verification

## Core Concepts

### Validation Pipeline (validator_models/automated_checks.py)
Multi-stage validation with fail-fast behavior:
1. **Pre-checks**: Schema, duplicates, blacklist
2. **Email validation**: Format, domain age, TrueList deliverability
3. **Company verification**: Website, LinkedIn profile matching
4. **Reputation scoring**: Domain history, SEC filings, press coverage (0-48 points)

### Consensus Protocol
- Commit/reveal protocol prevents validator collusion
- 3 validators per lead, majority agreement required
- ~72 minute epochs (360 blocks)

### Rate Limits
- 500 submission attempts per day per miner
- 100 rejections per day per miner
- Resets at 12:00 AM EST

## Testing

No formal test suite currently. Key files to manually test:
- `validator_models/automated_checks.py` - Validation logic
- `gateway/api/submit.py` - Lead submission endpoint
- `neurons/validator.py` - Validator consensus participation
