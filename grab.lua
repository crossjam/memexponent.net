-- grab.lua
-- Usage in .qmd: 
--   ::: {grab src="source.qmd#my-block"}
--   :::
--
-- This replaces the div with the block (or blocks) from source.qmd that has id "my-block".
-- Supported target types: Div, Header, CodeBlock, Para, BlockQuote, Table, Span (Span becomes a Para).

local function read_doc(path)
  local f, err = io.open(path, "r")
  if not f then
    return nil, ("grab.lua: cannot open file: %s (%s)"):format(path, err or "")
  end
  local content = f:read("*a"); f:close()
  -- Parse as Markdown; Quarto will still apply its pipeline to the caller doc
  local doc = pandoc.read(content, "markdown")
  return doc, nil
end

local function matches_id(el, id)
  -- Pandoc Lua ≥ 3: elements have .identifier; fallback for older via el.attr
  if el.identifier and el.identifier == id then return true end
  if el.attr and el.attr.identifier and el.attr.identifier == id then return true end
  return false
end

local function find_by_id(doc, id)
  local found = nil
  doc:walk({
    Div = function(el) if matches_id(el, id) then found = el end end,
    Header = function(el) if matches_id(el, id) then found = el end end,
    CodeBlock = function(el) if matches_id(el, id) then found = el end end,
    Span = function(el) if matches_id(el, id) then found = el end end,
    Para = function(el) if matches_id(el, id) then found = el end end,
    BlockQuote = function(el) if matches_id(el, id) then found = el end end,
    Table = function(el) if matches_id(el, id) then found = el end end,
  })
  return found
end

local function replace_with(node)
  if not node then return nil end
  if node.t == "Div" then
    -- Return the inner content of the div (i.e., the blocks inside it)
    return node.content
  elseif node.t == "Span" then
    -- Wrap span inline content as a paragraph for block context
    return { pandoc.Para(node.content) }
  else
    -- For single block nodes, return as a list of blocks
    return { node }
  end
end

local function split_src(src)
  -- split "path#frag" into (path, frag) where frag may be nil
  local path, frag = src:match("^([^#]+)#(.+)$")
  if not path then path = src end
  return path, frag
end

return {
  {
    Div = function(el)
      -- Trigger only on: ::: {grab src="file#id"}
      if not el.classes or not el.classes:includes("grab") then return nil end
      local src = el.attributes and el.attributes["src"] or nil
      if not src or src == "" then
        return { pandoc.Para{ pandoc.Str("grab.lua: missing src attribute") } }
      end

      local path, frag = split_src(src)
      local doc, err = read_doc(path)
      if not doc then
        return { pandoc.Para{ pandoc.Str(err or ("grab.lua: failed to read "..path)) } }
      end

      local node
      if frag and frag ~= "" then
        node = find_by_id(doc, frag)
        if not node then
          return { pandoc.Para{ pandoc.Str(("grab.lua: id not found: %s"):format(frag)) } }
        end
        return replace_with(node)
      else
        -- If no #id, include the whole parsed document body
        return doc.blocks
      end
    end
  }
}
