-- The API the rewriter tests run against, with its OpenAPI spec in the comments
-- process_openapi.py reads, as a HAF app declares its endpoints. The tests'
-- Dockerfile runs process_openapi.py on this file: the SQL it writes (the function
-- signatures filled in) is the database PostgREST serves, the rewrite rules and
-- argument map it writes are the rewriter's.
--
-- Every function answers three rows (col 1, 2, 3) naming the argument it got and
-- the method PostgREST was called with, so column filters, select, order, limit
-- and offset have something to act on.

/** openapi
openapi: 3.1.0
info:
  title: Rewriter fixture
  version: 1.0.0
*/

CREATE SCHEMA rewriter_fixture;

/** openapi:components:schemas
rewriter_fixture.echo_row:
  type: object
  properties:
    name:
      type: string
    col:
      type: integer
    method:
      type: string
*/
-- openapi-generated-code-begin
-- openapi-generated-code-end

/** openapi:components:schemas
rewriter_fixture.array_of_echo_row:
  type: array
  items:
    $ref: '#/components/schemas/rewriter_fixture.echo_row'
*/

/** openapi:paths
/accounts/{name}:
  get:
    operationId: rewriter_fixture.f
    parameters:
      - in: path
        name: name
        required: true
        schema:
          type: string
    responses:
      '200':
        description: The name, three times
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/rewriter_fixture.array_of_echo_row'
*/
-- openapi-generated-code-begin
-- openapi-generated-code-end
LANGUAGE sql STABLE
AS $$
  SELECT name, col, current_setting('request.method', true)
  FROM generate_series(1, 3) AS col
$$;

-- A declared argument whose name PostgREST reserves: an argument all the same.
/** openapi:paths
/limits:
  get:
    operationId: rewriter_fixture.p
    parameters:
      - in: query
        name: limit
        required: true
        schema:
          type: string
    responses:
      '200':
        description: The limit, three times
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/rewriter_fixture.array_of_echo_row'
*/
-- openapi-generated-code-begin
-- openapi-generated-code-end
LANGUAGE sql STABLE
AS $$
  SELECT "limit", col, current_setting('request.method', true)
  FROM generate_series(1, 3) AS col
$$;

/** openapi:paths
/version:
  get:
    operationId: rewriter_fixture.v
    responses:
      '200':
        description: The fixture's version
        content:
          application/json:
            schema:
              type: string
*/
-- openapi-generated-code-begin
-- openapi-generated-code-end
LANGUAGE sql STABLE
AS $$
  SELECT '1.0.0'::text
$$;

-- In no spec, so in no argument map: the rewriter forwards its calls unchanged.
CREATE FUNCTION rewriter_fixture.g(name text)
RETURNS SETOF rewriter_fixture.echo_row
LANGUAGE sql STABLE
AS $$
  SELECT name, col, current_setting('request.method', true)
  FROM generate_series(1, 3) AS col
$$;

CREATE ROLE rewriter_anon NOLOGIN;
GRANT USAGE ON SCHEMA rewriter_fixture TO rewriter_anon;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA rewriter_fixture TO rewriter_anon;
