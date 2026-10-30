from __future__ import annotations

import hashlib
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from benchbox.utils.printing import emit

from .rows import PipeRowWriter

FACT_TRADE_GENERATION_ALGORITHM_VERSION = 1


def _trade_record_rng(generation_seed: int, trade_id: int) -> random.Random:
    digest = hashlib.sha256(f"{generation_seed}:FactTrade:{trade_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


class FactGenerationMixin:
    def _generate_facttrade_data(self) -> str:
        file_path = self.output_dir / "FactTrade.tbl"
        num_trades = int(self.base_trades * self.scale_factor)
        num_accounts = int(self.base_accounts * self.scale_factor)
        num_securities = int(self.base_securities * self.scale_factor)
        num_customers = int(self.base_customers * self.scale_factor)
        num_companies = int(self.base_companies * self.scale_factor)

        if num_trades > 100000 and self.max_workers > 1:
            return self._generate_facttrade_parallel(
                file_path,
                num_trades,
                num_accounts,
                num_securities,
                num_customers,
                num_companies,
            )

        with open(file_path, "w", newline="", buffering=self.buffer_size, encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for chunk_start in range(1, num_trades + 1, self.chunk_size):
                chunk_end = min(chunk_start + self.chunk_size, num_trades + 1)
                chunk_rows = []

                for i in range(chunk_start, chunk_end):
                    rng = _trade_record_rng(self.generation_seed, i)
                    chunk_rows.append(
                        self._build_trade_row(
                            i,
                            rng,
                            num_accounts,
                            num_securities,
                            num_customers,
                            num_companies,
                        )
                    )

                writer.writerows(chunk_rows)
                if self.enable_progress:
                    self.logger.info(f"FactTrade: generated {chunk_end - 1:,} of {num_trades:,} records")
                self.generation_stats["chunks_processed"] += 1

                if self._check_memory_usage():
                    self._cleanup_memory()

        self.generation_stats["records_generated"] += num_trades
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_facttrade_parallel(
        self,
        file_path: Path,
        num_trades: int,
        num_accounts: int,
        num_securities: int,
        num_customers: int,
        num_companies: int,
    ) -> str:
        if self.enable_progress:
            self.logger.info(f"Using parallel generation with {self.max_workers} workers")

        chunks = [(i, min(i + self.chunk_size, num_trades + 1)) for i in range(1, num_trades + 1, self.chunk_size)]

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_chunk = {}

            for chunk_start, chunk_end in chunks:
                future = executor.submit(
                    self._generate_trade_chunk,
                    chunk_start,
                    chunk_end,
                    num_accounts,
                    num_securities,
                    num_customers,
                    num_companies,
                )
                future_to_chunk[future] = (chunk_start, chunk_end)

            with open(file_path, "w", newline="", buffering=self.buffer_size, encoding="utf-8") as f:
                writer = PipeRowWriter(f)

                completed_chunks = []
                for future in as_completed(future_to_chunk):
                    chunk_start, chunk_end = future_to_chunk[future]
                    try:
                        chunk_data = future.result()
                        completed_chunks.append((chunk_start, chunk_data))
                    except Exception as e:
                        emit(f"Error generating chunk {chunk_start}-{chunk_end}: {e}")
                        raise

                completed_chunks.sort(key=lambda x: x[0])
                for _, chunk_data in completed_chunks:
                    writer.writerows(chunk_data)

        self.generation_stats["records_generated"] += num_trades
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_trade_chunk(
        self,
        start_id: int,
        end_id: int,
        num_accounts: int,
        num_securities: int,
        num_customers: int,
        num_companies: int,
    ) -> list[list]:
        chunk_data = []

        for i in range(start_id, end_id):
            rng = _trade_record_rng(self.generation_seed, i)
            chunk_data.append(
                self._build_trade_row(
                    i,
                    rng,
                    num_accounts,
                    num_securities,
                    num_customers,
                    num_companies,
                )
            )

        return chunk_data

    def _build_trade_row(
        self,
        trade_id: int,
        rng: random.Random,
        num_accounts: int,
        num_securities: int,
        num_customers: int,
        num_companies: int,
    ) -> list:
        sk_broker_id = rng.randint(1, 100)
        sk_create_date_id = rng.randint(1, 5844)
        sk_create_time_id = rng.randint(1, 288)
        sk_close_date_id = sk_create_date_id + rng.randint(0, 5)
        sk_close_time_id = rng.randint(1, 288)

        status = rng.choices(["Completed", "Pending", "Cancelled"], weights=[0.8, 0.1, 0.1])[0]
        trade_type = rng.choice(self._trade_types)
        cash_flag = rng.choice([True, False])
        sk_security_id = rng.randint(1, num_securities)
        sk_company_id = rng.randint(1, num_companies)
        quantity = rng.randint(1, 10000)
        bid_price = round(rng.uniform(10.0, 500.0), 2)
        sk_customer_id = rng.randint(1, num_customers)
        sk_account_id = rng.randint(1, num_accounts)
        executed_by = f"Broker{rng.randint(1, 100)}"
        trade_price = round(bid_price * rng.uniform(0.98, 1.02), 2)
        fee = round(rng.uniform(5.0, 50.0), 2)
        commission = round(trade_price * quantity * 0.001, 2)
        tax = round(trade_price * quantity * 0.01, 2) if status == "Completed" else 0.0
        batch_id = 1

        return [
            trade_id,
            sk_broker_id,
            sk_create_date_id,
            sk_create_time_id,
            sk_close_date_id,
            sk_close_time_id,
            status,
            trade_type,
            cash_flag,
            sk_security_id,
            sk_company_id,
            quantity,
            bid_price,
            sk_customer_id,
            sk_account_id,
            executed_by,
            trade_price,
            fee,
            commission,
            tax,
            batch_id,
        ]

    def _generate_factcashbalances_data(self) -> str:
        file_path = self.output_dir / "FactCashBalances.tbl"
        num_customers = int(self.base_customers * self.scale_factor)
        num_accounts = int(self.base_accounts * self.scale_factor)

        num_records = min(num_accounts * 30, 100000)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for _i in range(num_records):
                sk_customer_id = self._rng.randint(1, num_customers)
                sk_account_id = self._rng.randint(1, num_accounts)
                sk_date_id = self._rng.randint(1, 5844)

                customer_tier = self.financial_patterns.generate_customer_tier()
                if customer_tier == 1:
                    cash_balance = round(self._rng.uniform(10000, 500000), 2)
                elif customer_tier == 2:
                    cash_balance = round(self._rng.uniform(1000, 50000), 2)
                else:
                    cash_balance = round(self._rng.uniform(100, 10000), 2)

                batch_id = 1

                row = [
                    sk_customer_id,
                    sk_account_id,
                    sk_date_id,
                    cash_balance,
                    batch_id,
                ]
                writer.writerow(row)

        self.generation_stats["records_generated"] += num_records
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_factholdings_data(self) -> str:
        file_path = self.output_dir / "FactHoldings.tbl"
        num_customers = int(self.base_customers * self.scale_factor)
        num_accounts = int(self.base_accounts * self.scale_factor)
        num_securities = int(self.base_securities * self.scale_factor)
        num_companies = int(self.base_companies * self.scale_factor)

        num_records = min(num_accounts * 10, 50000)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for _i in range(num_records):
                sk_customer_id = self._rng.randint(1, num_customers)
                sk_account_id = self._rng.randint(1, num_accounts)
                sk_security_id = self._rng.randint(1, num_securities)
                sk_company_id = self._rng.randint(1, num_companies)
                sk_date_id = self._rng.randint(1, 5844)
                sk_time_id = self._rng.randint(1, 288)

                current_price = round(self.financial_patterns.generate_security_price(), 2)

                customer_tier = self.financial_patterns.generate_customer_tier()
                current_holding = self.financial_patterns.generate_trade_quantity(customer_tier, current_price)

                batch_id = 1

                row = [
                    sk_customer_id,
                    sk_account_id,
                    sk_security_id,
                    sk_company_id,
                    sk_date_id,
                    sk_time_id,
                    current_price,
                    current_holding,
                    batch_id,
                ]
                writer.writerow(row)

        self.generation_stats["records_generated"] += num_records
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_factmarkethistory_data(self) -> str:
        file_path = self.output_dir / "FactMarketHistory.tbl"
        num_securities = int(self.base_securities * self.scale_factor)
        num_companies = int(self.base_companies * self.scale_factor)

        days_of_data = min(252, int(252 * self.scale_factor))
        num_records = min(num_securities * days_of_data, 100000)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            security_prices = {}

            for _i in range(num_records):
                sk_security_id = self._rng.randint(1, num_securities)
                sk_company_id = self._rng.randint(1, num_companies)
                sk_date_id = self._rng.randint(1, 5844)

                if sk_security_id not in security_prices:
                    base_price = self.financial_patterns.generate_security_price()
                    security_prices[sk_security_id] = base_price
                else:
                    base_price = security_prices[sk_security_id]
                    base_price = self.financial_patterns.generate_security_price(base_price)
                    security_prices[sk_security_id] = base_price

                close_price = round(base_price, 2)
                day_high = round(base_price * self._rng.uniform(1.0, 1.05), 2)
                day_low = round(base_price * self._rng.uniform(0.95, 1.0), 2)

                pe_ratio = round(self._rng.uniform(5, 50), 2) if self._rng.random() > 0.1 else None
                dividend_yield = round(self._rng.uniform(0, 0.08), 4)

                fifty_two_week_high = round(close_price * self._rng.uniform(1.1, 2.0), 2)
                fifty_two_week_low = round(close_price * self._rng.uniform(0.5, 0.9), 2)
                sk_52week_high_date = self._rng.randint(1, 5844)
                sk_52week_low_date = self._rng.randint(1, 5844)

                dividend_per_share = round(close_price * dividend_yield / 4, 4)
                volume = self.financial_patterns.generate_trading_volume()
                batch_id = 1

                row = [
                    sk_security_id,
                    sk_company_id,
                    sk_date_id,
                    pe_ratio,
                    dividend_yield,
                    fifty_two_week_high,
                    sk_52week_high_date,
                    fifty_two_week_low,
                    sk_52week_low_date,
                    dividend_per_share,
                    close_price,
                    day_high,
                    day_low,
                    volume,
                    batch_id,
                ]
                writer.writerow(row)

        self.generation_stats["records_generated"] += num_records
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)

    def _generate_factwatches_data(self) -> str:
        file_path = self.output_dir / "FactWatches.tbl"
        num_customers = int(self.base_customers * self.scale_factor)
        num_securities = int(self.base_securities * self.scale_factor)

        num_records = min(int(num_customers * 5 * self.scale_factor), 25000)

        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = PipeRowWriter(f)

            for _i in range(num_records):
                sk_customer_id = self._rng.randint(1, num_customers)
                sk_security_id = self._rng.randint(1, num_securities)
                sk_date_placed = self._rng.randint(1, 5844)

                sk_date_removed = self._rng.randint(sk_date_placed, 5844) if self._rng.random() < 0.3 else None

                batch_id = 1

                row = [
                    sk_customer_id,
                    sk_security_id,
                    sk_date_placed,
                    sk_date_removed,
                    batch_id,
                ]
                writer.writerow(row)

        self.generation_stats["records_generated"] += num_records
        file_path = self.compress_existing_file(file_path, remove_original=True)
        return str(file_path)


__all__ = ["FactGenerationMixin"]
