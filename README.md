# Stadtbibliothek -- Home Assistant Integration

Custom component for Stuttgart and Remseck public library accounts.

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz)
[![HA Version](https://img.shields.io/badge/HA-2024.1%2B-blue)](https://www.home-assistant.io)

## Features

- Loan overview for multiple library accounts
- Warning sensor (days until earliest due date, overdue detection)
- Fee tracking
- Loan renewal services (single item or all)
- Supports Stuttgart (aDIS/BMS) and Remseck (Koha/LMSCloud)

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu -> Custom repositories
3. Add `https://github.com/makefu/ha_stadtbibliothek` as an Integration
4. Search for "Stadtbibliothek" and install

### Manual

Copy `custom_components/stadtbibliothek/` into your Home Assistant `config/custom_components/` directory and restart.

## Configuration

1. Go to **Settings -> Integrations -> Add Integration**
2. Search for **Stadtbibliothek**
3. Select library type: Remseck (Koha) or Stuttgart (aDIS)
4. Enter your library card number and password
5. Repeat to add multiple accounts

## Sensors

Each configured account creates three sensors:

### Loans (`sensor.stadtbibliothek_*_loans`)

- **State:** number of active loans
- **Attributes:**
  - `loans` -- list of loan objects containing: `title`, `item_id`, `due_date`, `can_be_renewed`, `days_remaining`, `is_overdue`, plus optional fields like `author`, `checkout_date`, `media_type`, `library_branch`, `times_renewed`, `max_renewals`, `renewals_left`, `call_number`

### Warning (`sensor.stadtbibliothek_*_warning`)

- **State:** days until earliest due date (negative = overdue), `none` if no loans
- **Attributes:**
  - `overdue_count` -- number of overdue items
  - `items_due_soon` -- items due within 7 days
  - `earliest_due_date` -- ISO date of earliest due item
  - `refresh_required` -- bool, indicates a manual HA refresh is recommended after renewals

### Fees (`sensor.stadtbibliothek_*_fees`)

- **State:** total outstanding fees in EUR
- **Attributes:**
  - `fee_items` -- list of individual fee entries

## Services

### `stadtbibliothek.renew_loan`

Renew a specific item by its barcode/item ID.

| Field | Description |
|-------|-------------|
| `config_entry_id` | The config entry of the library account |
| `item_id` | Barcode or item ID to renew |

### `stadtbibliothek.renew_all`

Renew all renewable items for a library account.

| Field | Description |
|-------|-------------|
| `config_entry_id` | The config entry of the library account |
| `days_remaining_threshold` | *(optional, default 14)* Only renew items due within this many days (0-90) |

### `stadtbibliothek.force_update`

Force an immediate data refresh.

| Field | Description |
|-------|-------------|
| `config_entry_id` | The config entry of the library account |

## CLI

Standalone command-line tools for querying and managing library loans without Home Assistant.

### Installation

```sh
nix run .#stadtbibliothek-remseck -- --help
nix run .#stadtbibliothek-stuttgart -- --help
```

### Usage

```sh
# Show loan status (human-readable)
stadtbibliothek-remseck status --username 12345 --password mypin

# Show loan status (JSON)
stadtbibliothek-remseck status --username 12345 --password mypin --json

# Renew specific items
stadtbibliothek-remseck renew --username 12345 --password mypin --item-id ABC123 --item-id DEF456
```

Replace `remseck` with `stuttgart` for Stuttgart accounts.

## Automation Examples

All examples below can be pasted directly into the Home Assistant automation editor (YAML mode). Replace `sensor.stadtbibliothek_remseck_12345_*` with your actual sensor entity IDs.

> **Finding your `config_entry_id`:** Go to **Settings → Integrations → Stadtbibliothek**, click the three-dot menu on your account, and select **System options**. The URL will contain the config entry ID, e.g. `…/config_entry/abcdef1234567890`. You can also find it via **Developer Tools → Services** when calling a Stadtbibliothek service.

### Notify when items are due within 3 days

```yaml
alias: "Library due date warning"
trigger:
  - platform: numeric_state
    entity_id: sensor.stadtbibliothek_remseck_12345_warning
    below: 4
action:
  - service: notify.mobile_app
    data:
      title: "Library books due soon"
      message: >
        {{ state_attr('sensor.stadtbibliothek_remseck_12345_warning', 'items_due_soon') }}
        item(s) due within 7 days.
        Earliest due: {{ state_attr('sensor.stadtbibliothek_remseck_12345_warning', 'earliest_due_date') }}
```

### Send loan list via notification

```yaml
alias: "Weekly library loan summary"
trigger:
  - platform: time
    at: "09:00:00"
condition:
  - condition: time
    weekday: [mon]
action:
  - service: notify.mobile_app
    data:
      title: "Library loans ({{ states('sensor.stadtbibliothek_remseck_12345_loans') }})"
      message: >
        {% for loan in state_attr('sensor.stadtbibliothek_remseck_12345_loans', 'loans') %}
        - {{ loan.title }}{% if loan.author %} ({{ loan.author }}){% endif %} — due {{ loan.due_date }}{% if loan.is_overdue %} ⚠ OVERDUE{% endif %}, {{ loan.renewals_left }} renewals left
        {% endfor %}
```

### Auto-renew all items when due within 3 days

```yaml
alias: "Auto-renew library books"
trigger:
  - platform: numeric_state
    entity_id: sensor.stadtbibliothek_remseck_12345_warning
    below: 4
action:
  - service: stadtbibliothek.renew_all
    data:
      config_entry_id: "abcdef1234567890"
      days_remaining_threshold: 3
    response_variable: renew_result
  - service: notify.mobile_app
    data:
      title: "{{ renew_result.renewed }}/{{ renew_result.total_attempted }} verlängert"
      message: >
        {% for r in renew_result.results %}
        {% if r.success %}✅{% else %}❌{% endif %} {{ r.title }}{% if r.error %} — {{ r.error }}{% endif %}
        {% endfor %}
```

### Renew individual loans and notify results

```yaml
alias: "Renew individual loans"
trigger:
  - platform: time
    at: "08:00:00"
action:
  - repeat:
      for_each: >
        {{ state_attr('sensor.stadtbibliothek_remseck_12345_loans', 'loans')
           | selectattr('days_remaining', 'lt', 3)
           | selectattr('can_be_renewed')
           | list }}
      sequence:
        - service: stadtbibliothek.renew_loan
          data:
            config_entry_id: "abcdef1234567890"
            item_id: "{{ repeat.item.item_id }}"
          response_variable: result
        - service: notify.mobile_app
          data:
            title: >
              {% if result.success %}✅ Verlängert{% else %}❌ Fehlgeschlagen{% endif %}
            message: >
              {{ repeat.item.title }}{% if result.error %} — {{ result.error }}{% endif %}
```

## Development

```sh
nix develop                  # dev shell with all dependencies
pytest tests/ --ignore=tests/integration   # unit tests

# live integration tests (require credentials)
nix run .#integration-remseck -- /path/to/.secrets.yml
SECRETS_FILE=/path/to/.secrets.yml nix run .#integration-stuttgart

# NixOS VM test
nix build .#checks.x86_64-linux.vm-test
```

## License

Inspired by [bibliotheek_be](https://github.com/myTselworern/bibliotheek_be) by @myTselection.
