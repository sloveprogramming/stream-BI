{#
  让 model config 里的 `schema=` 被原样使用。

  dbt 默认行为是 `target.schema + '_' + custom_schema`，
  于是 `+schema: dwd` 会变成 `dwd_dwd` —— 与项目约定的 dwd/dws/ads 分层不符。
  这里覆盖成「自定义 schema 原样使用」，与权限元数据里的 source_table 保持一致。
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
