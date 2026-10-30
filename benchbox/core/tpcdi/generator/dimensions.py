from __future__ import annotations

from datetime import datetime, time, timedelta

from ..financial_data import generate_realistic_tax_rates
from .rows import PipeRowWriter


class DimensionGenerationMixin:
    def _generate_dimdate_data(self) -> str:
        file_path = self.output_dir / "DimDate.tbl"

        start_date = datetime(2010, 1, 1)
        end_date = datetime(2025, 12, 31)
        total_days = (end_date - start_date).days + 1

        with open(file_path, "w", newline="", buffering=self.buffer_size, encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            current_date = start_date
            sk_date_id = 1
            chunk_buffer = []

            while current_date <= end_date:
                date_value = current_date.date()
                date_desc = current_date.strftime("%Y-%m-%d")

                calendar_year_id = current_date.year
                calendar_year_desc = str(current_date.year)
                calendar_qtr_id = (current_date.month - 1) // 3 + 1
                calendar_qtr_desc = f"Q{calendar_qtr_id} {current_date.year}"
                calendar_month_id = current_date.month
                calendar_month_desc = current_date.strftime("%B %Y")
                calendar_week_id = current_date.isocalendar()[1]
                calendar_week_desc = f"Week {calendar_week_id} {current_date.year}"
                day_of_week_num = current_date.weekday() + 1
                day_of_week_desc = current_date.strftime("%A")

                fiscal_year_id = calendar_year_id
                fiscal_year_desc = calendar_year_desc
                fiscal_qtr_id = calendar_qtr_id
                fiscal_qtr_desc = calendar_qtr_desc

                holiday_flag = (
                    (current_date.month == 1 and current_date.day == 1)
                    or (current_date.month == 7 and current_date.day == 4)
                    or (current_date.month == 12 and current_date.day == 25)
                )

                row = [
                    sk_date_id,
                    date_value,
                    date_desc,
                    calendar_year_id,
                    calendar_year_desc,
                    calendar_qtr_id,
                    calendar_qtr_desc,
                    calendar_month_id,
                    calendar_month_desc,
                    calendar_week_id,
                    calendar_week_desc,
                    day_of_week_num,
                    day_of_week_desc,
                    fiscal_year_id,
                    fiscal_year_desc,
                    fiscal_qtr_id,
                    fiscal_qtr_desc,
                    holiday_flag,
                ]

                chunk_buffer.append(row)

                if len(chunk_buffer) >= self.chunk_size:
                    writer.writerows(chunk_buffer)
                    chunk_buffer.clear()
                    if self.enable_progress and sk_date_id % 1000 == 0:
                        self.logger.info(f"DimDate: generated {sk_date_id:,} of {total_days:,} records")

                current_date += timedelta(days=1)
                sk_date_id += 1

            if chunk_buffer:
                writer.writerows(chunk_buffer)

        self.generation_stats["records_generated"] += total_days
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimtime_data(self) -> str:
        file_path = self.output_dir / "DimTime.tbl"

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            sk_time_id = 1

            for hour in range(24):
                for minute in range(0, 60, 5):
                    time_value = time(hour, minute)
                    hour_desc = f"Hour {hour:02d}"
                    minute_desc = f"Minute {minute:02d}"
                    second_desc = "Second 00"

                    market_hours_flag = (9 <= hour < 16) or (hour == 9 and minute >= 30)

                    office_hours_flag = 8 <= hour < 18

                    row = [
                        sk_time_id,
                        time_value,
                        hour,
                        hour_desc,
                        minute,
                        minute_desc,
                        0,
                        second_desc,
                        market_hours_flag,
                        office_hours_flag,
                    ]

                    writer.writerow(row)
                    sk_time_id += 1

        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimcompany_data(self) -> str:
        file_path = self.output_dir / "DimCompany.tbl"
        num_companies = int(self.base_companies * self.scale_factor)

        with open(file_path, "w", newline="", buffering=self.buffer_size, encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for chunk_start in range(1, num_companies + 1, self.chunk_size):
                chunk_end = min(chunk_start + self.chunk_size, num_companies + 1)
                chunk_rows = []

                for i in range(chunk_start, chunk_end):
                    sk_company_id = i
                    company_id = i
                    status = self._rng.choice(self._statuses)
                    name = f"Company {i:04d} Inc."
                    industry = self._rng.choice(self._industries)
                    sp_rating = self._rng.choice(self._sp_ratings)
                    is_low_grade = sp_rating in ["BB+", "BB", "BB-"]
                    base_market_cap = self._rng.uniform(100_000_000, 50_000_000_000)
                    if not is_low_grade:
                        market_cap = round(base_market_cap * self._rng.uniform(1.2, 2.0), 2)
                    else:
                        market_cap = round(base_market_cap * self._rng.uniform(0.5, 1.0), 2)
                    ceo = f"CEO {i:04d}"

                    address_line1 = f"{self._rng.randint(1, 9999)} Main Street"
                    address_line2 = ""
                    postal_code = f"{self._rng.randint(10000, 99999)}"
                    city = f"City{i % 100}"
                    state_prov = self._rng.choice(self._us_states)
                    country = "USA"
                    description = f"Description for {name}"
                    founding_date = datetime(
                        self._rng.randint(1950, 2020),
                        self._rng.randint(1, 12),
                        self._rng.randint(1, 28),
                    ).date()

                    is_current = True
                    batch_id = 1
                    effective_date = datetime(2010, 1, 1).date()
                    end_date = datetime(9999, 12, 31).date()

                    row = [
                        sk_company_id,
                        company_id,
                        status,
                        name,
                        industry,
                        sp_rating,
                        is_low_grade,
                        market_cap,
                        ceo,
                        address_line1,
                        address_line2,
                        postal_code,
                        city,
                        state_prov,
                        country,
                        description,
                        founding_date,
                        is_current,
                        batch_id,
                        effective_date,
                        end_date,
                    ]

                    chunk_rows.append(row)

                writer.writerows(chunk_rows)
                if self.enable_progress:
                    self.logger.info(f"DimCompany: generated {chunk_end - 1:,} of {num_companies:,} records")
                self.generation_stats["chunks_processed"] += 1

                if self._check_memory_usage():
                    self._cleanup_memory()

        self.generation_stats["records_generated"] += num_companies
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimsecurity_data(self) -> str:
        file_path = self.output_dir / "DimSecurity.tbl"
        num_securities = int(self.base_securities * self.scale_factor)
        num_companies = int(self.base_companies * self.scale_factor)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for i in range(1, num_securities + 1):
                sk_security_id = i
                symbol = f"SYM{i:04d}"
                issue = "S"
                status = self._rng.choice(self._statuses)
                name = f"Security {i:04d}"
                exchange_id = self._rng.choice(["NYSE", "NASDAQ", "AMEX"])
                sk_company_id = self._rng.randint(1, num_companies)
                shares_outstanding = self._rng.randint(1000000, 1000000000)
                first_trade = datetime(
                    self._rng.randint(2000, 2020),
                    self._rng.randint(1, 12),
                    self._rng.randint(1, 28),
                ).date()
                first_trade_on_exchange = first_trade
                dividend = round(self._rng.uniform(0, 5.0), 2)

                is_current = True
                batch_id = 1
                effective_date = datetime(2010, 1, 1).date()
                end_date = datetime(9999, 12, 31).date()

                row = [
                    sk_security_id,
                    symbol,
                    issue,
                    status,
                    name,
                    exchange_id,
                    sk_company_id,
                    shares_outstanding,
                    first_trade,
                    first_trade_on_exchange,
                    dividend,
                    is_current,
                    batch_id,
                    effective_date,
                    end_date,
                ]

                writer.writerow(row)

        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimcustomer_data(self) -> str:
        file_path = self.output_dir / "DimCustomer.tbl"
        num_customers = int(self.base_customers * self.scale_factor)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for i in range(1, num_customers + 1):
                sk_customer_id = i
                customer_id = i
                tax_id = f"{self._rng.randint(100000000, 999999999)}"
                status = self._rng.choice(self._statuses)
                last_name = f"LastName{i:05d}"
                first_name = f"FirstName{i:05d}"
                middle_initial = self._rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                gender = self._rng.choice(["M", "F"])
                tier = self._rng.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
                dob = datetime(
                    self._rng.randint(1930, 2000),
                    self._rng.randint(1, 12),
                    self._rng.randint(1, 28),
                ).date()

                address_line1 = f"{self._rng.randint(1, 9999)} Customer Street"
                address_line2 = ""
                postal_code = f"{self._rng.randint(10000, 99999)}"
                city = f"City{i % 500}"
                state_prov = self._rng.choice(self._us_states)
                country = "USA"

                phone1 = f"{self._rng.randint(100, 999)}-{self._rng.randint(100, 999)}-{self._rng.randint(1000, 9999)}"
                phone2 = ""
                phone3 = ""
                email1 = f"customer{i}@email.com"
                email2 = ""

                national_tax_rate_desc = "Federal Tax"
                national_tax_rate = round(self._rng.uniform(0.15, 0.35), 5)
                local_tax_rate_desc = "State Tax"
                local_tax_rate = round(self._rng.uniform(0.0, 0.10), 5)

                agency_id = "AGY001"
                credit_rating = self._rng.randint(300, 850)
                net_worth = self._rng.randint(10000, 10000000)
                marketing_nameplate = f"Customer {i}"

                is_current = True
                batch_id = 1
                effective_date = datetime(2010, 1, 1).date()
                end_date = datetime(9999, 12, 31).date()

                row = [
                    sk_customer_id,
                    customer_id,
                    tax_id,
                    status,
                    last_name,
                    first_name,
                    middle_initial,
                    gender,
                    tier,
                    dob,
                    address_line1,
                    address_line2,
                    postal_code,
                    city,
                    state_prov,
                    country,
                    phone1,
                    phone2,
                    phone3,
                    email1,
                    email2,
                    national_tax_rate_desc,
                    national_tax_rate,
                    local_tax_rate_desc,
                    local_tax_rate,
                    agency_id,
                    credit_rating,
                    net_worth,
                    marketing_nameplate,
                    is_current,
                    batch_id,
                    effective_date,
                    end_date,
                ]

                writer.writerow(row)

        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimaccount_data(self) -> str:
        file_path = self.output_dir / "DimAccount.tbl"
        num_accounts = int(self.base_accounts * self.scale_factor)
        num_customers = int(self.base_customers * self.scale_factor)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for i in range(1, num_accounts + 1):
                sk_account_id = i
                account_id = i
                sk_broker_id = self._rng.randint(1, 100)
                sk_customer_id = self._rng.randint(1, num_customers)
                status = self._rng.choice(self._statuses)
                account_desc = f"Account {i:06d}"
                tax_status = self._rng.randint(0, 2)

                is_current = True
                batch_id = 1
                effective_date = datetime(2010, 1, 1).date()
                end_date = datetime(9999, 12, 31).date()

                row = [
                    sk_account_id,
                    account_id,
                    sk_broker_id,
                    sk_customer_id,
                    status,
                    account_desc,
                    tax_status,
                    is_current,
                    batch_id,
                    effective_date,
                    end_date,
                ]

                writer.writerow(row)

        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_industry_data(self) -> str:
        file_path = self.output_dir / "Industry.tbl"

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            industry_data = self.financial_patterns.get_industry_data()
            for industry_id, industry_name, sector_code in industry_data:
                row = [industry_id, industry_name, sector_code]
                writer.writerow(row)

        self.generation_stats["records_generated"] += len(industry_data)
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_statustype_data(self) -> str:
        file_path = self.output_dir / "StatusType.tbl"

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            status_data = self.financial_patterns.get_status_types()
            for status_id, status_name in status_data:
                row = [status_id, status_name]
                writer.writerow(row)

        self.generation_stats["records_generated"] += len(status_data)
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_taxrate_data(self) -> str:
        file_path = self.output_dir / "TaxRate.tbl"

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            tax_data = generate_realistic_tax_rates()
            for tax_id, tax_name, tax_rate in tax_data:
                row = [tax_id, tax_name, tax_rate]
                writer.writerow(row)

        self.generation_stats["records_generated"] += len(tax_data)
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_tradetype_data(self) -> str:
        file_path = self.output_dir / "TradeType.tbl"

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            trade_data = self.financial_patterns.get_trade_types()
            for type_id, type_name, is_sell, is_market in trade_data:
                row = [type_id, type_name, is_sell, is_market]
                writer.writerow(row)

        self.generation_stats["records_generated"] += len(trade_data)
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_dimbroker_data(self) -> str:
        file_path = self.output_dir / "DimBroker.tbl"
        num_brokers = max(100, int(self.scale_factor * 50))

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            managers = []
            for i in range(1, min(21, num_brokers // 5) + 1):
                managers.append(i)

            for i in range(1, num_brokers + 1):
                sk_broker_id = i
                broker_id = i

                if i in managers:
                    manager_id = None
                else:
                    manager_id = self._rng.choice(managers)

                first_name = self._rng.choice(self.financial_patterns.first_names)
                last_name = self._rng.choice(self.financial_patterns.last_names)
                middle_initial = self._rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ")

                branch = f"Branch {((i - 1) // 20) + 1:02d}"
                office = f"Office {self._rng.choice(['A', 'B', 'C', 'D'])}"
                phone = f"{self._rng.randint(100, 999)}-{self._rng.randint(100, 999)}-{self._rng.randint(1000, 9999)}"

                is_current = True
                batch_id = 1
                effective_date = datetime(2010, 1, 1).date()
                end_date = datetime(9999, 12, 31).date()

                row = [
                    sk_broker_id,
                    broker_id,
                    manager_id,
                    first_name,
                    last_name,
                    middle_initial,
                    branch,
                    office,
                    phone,
                    is_current,
                    batch_id,
                    effective_date,
                    end_date,
                ]
                writer.writerow(row)

        self.generation_stats["records_generated"] += num_brokers
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)


__all__ = ["DimensionGenerationMixin"]
