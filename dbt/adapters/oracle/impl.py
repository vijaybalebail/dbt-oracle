from typing import (
    Optional, List
)

from dbt.adapters.sql import SQLAdapter
from dbt.adapters.base.meta import available
from dbt.adapters.oracle import OracleAdapterConnectionManager
from dbt.adapters.oracle.relation import OracleRelation

import agate

COLUMNS_EQUAL_SQL = '''
with diff_count as (
    SELECT
        1 as id,
        COUNT(*) as num_missing FROM (
            (SELECT {columns} FROM {relation_a} {except_op}
             SELECT {columns} FROM {relation_b})
             MINUS
            (SELECT {columns} FROM {relation_b} {except_op}
             SELECT {columns} FROM {relation_a})
        ) a
), table_a as (
    SELECT COUNT(*) as num_rows FROM {relation_a}
), table_b as (
    SELECT COUNT(*) as num_rows FROM {relation_b}
), row_count_diff as (
    select
        1 as id,
        table_a.num_rows - table_b.num_rows as difference
    from table_a, table_b
)
select
    row_count_diff.difference as row_count_difference,
    diff_count.num_missing as num_mismatched
from row_count_diff
join diff_count using (id)
'''.strip()

LIST_RELATIONS_MACRO_NAME = 'list_relations_without_caching'


class OracleAdapter(SQLAdapter):
    ConnectionManager = OracleAdapterConnectionManager
    Relation = OracleRelation

    def debug_query(self) -> None:
        self.execute("select 1 as id from dual")
        
    @classmethod
    def date_function(cls):
        return 'CURRENT_DATE'

    @classmethod
    def convert_text_type(cls, agate_table, col_idx):
        column = agate_table.columns[col_idx]
        lens = (len(d.encode("utf-8")) for d in column.values_without_nulls())
        max_len = max(lens) if lens else 64
        length = max_len if max_len > 16 else 16
        return "varchar2({})".format(length)

    @classmethod
    def convert_date_type(cls, agate_table, col_idx):
        return "timestamp"

    @classmethod
    def convert_datetime_type(cls, agate_table, col_idx):
        return "timestamp"

    @classmethod
    def convert_boolean_type(cls, agate_table, col_idx):
        return "number(1)"

    @classmethod
    def convert_number_type(cls, agate_table, col_idx):
        decimals = agate_table.aggregate(agate.MaxPrecision(col_idx))
        return "number"

    @classmethod
    def convert_time_type(cls, agate_table, col_idx):
        return "timestamp"

    @available
    def verify_database(self, database):
        if database.startswith('"'):
            database = database.strip('"')
        expected = self.config.credentials.database
        if database.lower() != expected.lower():
            raise dbt.exceptions.NotImplementedException(
                'Cross-db references not allowed in {} ({} vs {})'
                .format(self.type(), database, expected)
            )
        # return an empty string on success so macros can call this
        return ''

    def get_rows_different_sql(
        self,
        relation_a: OracleRelation,
        relation_b: OracleRelation,
        column_names: Optional[List[str]] = None,
        except_operator: str = 'MINUS',
    ) -> str:
        """Generate SQL for a query that returns a single row with a two
        columns: the number of rows that are different between the two
        relations and the number of mismatched rows.
        """
        # This method only really exists for test reasons.
        names: List[str]
        if column_names is None:
            columns = self.get_columns_in_relation(relation_a)
            #names = sorted((self.quote(c.name) for c in columns)
            names = sorted((c.name for c in columns))
        else:
            #names = sorted((self.quote(n) for n in column_names))
            names = sorted((n for n in column_names))
        columns_csv = ', '.join(names)

        sql = COLUMNS_EQUAL_SQL.format(
            columns=columns_csv,
            relation_a=str(relation_a),
            relation_b=str(relation_b),
            except_op=except_operator,
        )

        return sql


    def list_relations_without_caching(
        self, schema_relation: OracleRelation,
    ) -> List[OracleRelation]:
        # Had to implement this because of the hardcoded
        # quote_policy setting all to true in dbt core

        kwargs = {'schema_relation': schema_relation}
        results = self.execute_macro(
            LIST_RELATIONS_MACRO_NAME,
            kwargs=kwargs
        )

        quote_policy = {
            'database': False,
            'schema': False,
            'identifier': False
        }
        relations = []

        for _database, name, _schema, _type in results:
            try:
                _type = self.Relation.get_relation_type(_type)
            except ValueError:
                _type = self.Relation.External
                
            relations.append(self.Relation.create(
                database=_database,
                schema=_schema,
                identifier=name,
                quote_policy=quote_policy,
                type=_type
            ))
        return relations


    def timestamp_add_sql(
        self, add_to: str, number: int = 1, interval: str = 'hour'
    ) -> str:
        # for backwards compatibility, we're compelled to set some sort of
        # default. A lot of searching has lead me to believe that the
        # '+ interval' syntax used in postgres/redshift is relatively common
        # and might even be the SQL standard's intention.
        return f"{add_to} + interval '{number}' {interval}"