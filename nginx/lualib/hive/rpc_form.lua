-- Forwards a REST GET (or HEAD) of a PostgREST function as a form-encoded POST, so
-- each argument reaches the function literally.
--
-- PostgREST reads a GET query value that starts with an operator (`not.`, `eq.`,
-- `gt.`, ...) as a filter on the result, never as an argument, while every field
-- of a form-encoded POST is an argument. So the query parameters the endpoint
-- declares in its OpenAPI spec (its function's parameters, path segments the
-- rewrite rules move into the query included) go to the body, and every other
-- parameter stays in the query string, where it keeps its PostgREST meaning: a
-- column filter, `select`, `order`, `limit`, `offset`, `and`, `or`. The split
-- depends on the parameter's name and the function's declaration only, never on
-- the value. Each part keeps its encoding and its order.
--
-- `arguments` maps a function name to the set of its declared parameter names,
-- as process_openapi.py writes it to rpc_arguments.lua:
--
--   init_by_lua_block {
--       rpc_form = require "hive.rpc_form"
--       rpc_arguments = dofile(ngx.config.prefix() .. "conf/rpc_arguments.lua")
--   }
--   location / {
--       include rewrite_rules.conf;
--       rewrite_by_lua_block { rpc_form.forward(rpc_arguments) }
--       header_filter_by_lua_block { rpc_form.restore_head() }
--       proxy_pass http://app-postgrest:3000;
--   }
--
-- A request for a function the map does not name, or carrying none of its
-- declared arguments, is forwarded unchanged.
--
-- PostgREST runs a POST to a VOLATILE function READ WRITE: a function reached
-- through this must be STABLE or IMMUTABLE to stay read-only.

local _M = {}

local HEAD_CTX_KEY = "hive_rpc_form_head"

local function parameter_name(part)
    return ngx.unescape_uri((part:match("^[^=]*"):gsub("%+", " ")))
end

function _M.forward(arguments)
    local method = ngx.req.get_method()
    if method ~= "GET" and method ~= "HEAD" then
        return
    end
    local function_name = ngx.var.uri:match("^/rpc/([^/]+)$")
    local declared = function_name and arguments[function_name]
    if not declared then
        return
    end

    local body, query = {}, {}
    for part in (ngx.var.args or ""):gmatch("[^&]+") do
        if declared[parameter_name(part)] then
            body[#body + 1] = part
        else
            query[#query + 1] = part
        end
    end
    if #body == 0 then
        return
    end

    ngx.ctx[HEAD_CTX_KEY] = method == "HEAD"
    ngx.req.read_body()
    ngx.req.set_method(ngx.HTTP_POST)
    ngx.req.set_header("Content-Type", "application/x-www-form-urlencoded")
    ngx.req.set_body_data(table.concat(body, "&"))
    ngx.req.set_uri_args(table.concat(query, "&"))
end

-- nginx leaves a HEAD's answer without its body by the request's method when it
-- writes the headers, after forward() made it a POST. Giving a HEAD its method
-- back here makes its answer carry the status and headers of the GET and no body,
-- so the next answer on a keep-alive connection starts where the client expects it.
function _M.restore_head()
    if ngx.ctx[HEAD_CTX_KEY] then
        ngx.req.set_method(ngx.HTTP_HEAD)
    end
end

return _M
